"""Integracion con Retell AI para el asistente de voz.

Concentra todo lo que toca a Retell: que agente atiende a cada rol, la creacion
de la llamada web, la verificacion de la firma de las custom functions y la
resolucion del `call_id` al usuario que abrio la llamada. La API key se usa
solo aqui, del lado del servidor.

Una llamada vale lo mismo que la sesion web que la abrio (RNF5): deja de
autorizar funciones si la cuenta se desactiva o cambia de rol, si entra en
otro navegador, cierra sesion o cambia de contrasena (`huella_sesion`), si su
inicio de sesion queda bloqueado por intentos fallidos o si debe cambiar su
contrasena. La inactividad no la corta: cada funcion de voz y el latido del
navegador (`asistente.latido`) cuentan como actividad de esa sesion.
"""

import hashlib
from datetime import timedelta

from flask import current_app

from app.extensions import db
from app.models import VIGENCIA_SESION, Rol, SesionAsistente
from app.services import acceso
from app.tiempo import ahora, hoy

# Rol -> clave de configuracion con el agent_id de Retell que lo atiende. Un rol
# fuera del mapa (o con la clave vacia) no tiene asistente.
AGENTES_POR_ROL = {
    Rol.CONDUCTOR: "RETELL_AGENTE_CONDUCTOR_ID",
    Rol.DESPACHADOR: "RETELL_AGENTE_GESTOR_ID",
    Rol.ADMIN: "RETELL_AGENTE_ADMIN_ID",
    Rol.CLIENTE: "RETELL_AGENTE_CLIENTE_ID",
}

# Las sesiones vencidas se conservan un dia para poder auditar llamadas
# recientes; despues se purgan al crear la siguiente.
RETENCION_SESIONES = timedelta(days=1)


# Motivos por los que una llamada abierta deja de autorizar funciones. Gaby los
# lee al usuario: por eso van en segunda persona y sin detalles internos.
MOTIVO_VENCIDA = "La llamada ya no está autorizada; inicia una nueva desde la aplicación."
MOTIVO_INACTIVA = "Tu cuenta fue desactivada o cambió de rol, así que termino la llamada aquí."
MOTIVO_OTRA_SESION = ("Tu sesión se cerró: la cuenta inició sesión en otro lugar, cerró sesión o "
                      "cambió de contraseña. Termino la llamada aquí.")
MOTIVO_BLOQUEADA = ("El inicio de sesión de tu cuenta está bloqueado por intentos fallidos, así que "
                    "termino la llamada aquí. Podrás volver a entrar en un momento.")
MOTIVO_CAMBIO_CONTRASENA = ("Debes cambiar tu contraseña antes de seguir. Hazlo en pantalla, en Perfil, "
                            "y vuelve a llamar.")


def huella_sesion(usuario):
    """SHA-256 del identificador de la sesion web vigente de `usuario`."""
    return hashlib.sha256(usuario.get_id().encode("utf-8")).hexdigest()


class AsistenteNoDisponible(Exception):
    """El rol no tiene agente configurado o falta la API key."""


class ErrorRetell(Exception):
    """Retell no pudo crear la llamada web."""


def agente_para(rol):
    """agent_id de Retell para el rol, o None si el rol no tiene asistente."""
    config = current_app.config
    if not config.get("RETELL_API_KEY"):
        return None
    clave = AGENTES_POR_ROL.get(rol)
    return (config.get(clave) or None) if clave else None


def cliente_retell(timeout=10):
    """Cliente del SDK de Retell. Las pruebas lo reemplazan por uno simulado.

    Sin reintentos y, por defecto, con timeout corto: el conductor esta
    esperando frente al boton, y un reintento silencioso solo alarga la espera
    sin informarle. `sincronizar-asistentes` pide uno mas largo y maneja sus
    propios reintentos (ver app/asistentes/sincronizacion.py).
    """
    from retell import Retell

    return Retell(
        api_key=current_app.config["RETELL_API_KEY"],
        max_retries=0,
        timeout=timeout,
    )


def iniciar_llamada(usuario):
    """Crea la llamada web para `usuario` y registra su sesion del asistente.

    Devuelve los datos que el navegador necesita para unirse a la llamada. La
    respuesta de /v3/create-web-call usa el transporte "gateway", que exige el
    call_id y los servidores ICE ademas del access_token; ninguno de los
    cuatro da acceso a la cuenta de Retell.

    `fecha_hoy` (hora de Bogota) permite al agente convertir "manana" o "el
    viernes" en la fecha ISO que esperan las funciones.
    """
    agent_id = agente_para(usuario.rol)
    if agent_id is None:
        raise AsistenteNoDisponible(usuario.rol)

    try:
        llamada = cliente_retell().call.create_web_call(
            agent_id=agent_id,
            retell_llm_dynamic_variables={
                "nombre_usuario": usuario.nombre,
                "fecha_hoy": hoy().isoformat(),
            },
        )
    except Exception as error:
        current_app.logger.warning("Retell no creo la llamada web: %s", error)
        raise ErrorRetell(str(error)) from error

    momento = ahora()
    db.session.query(SesionAsistente).filter(
        SesionAsistente.vence_en < momento - RETENCION_SESIONES
    ).delete(synchronize_session=False)
    db.session.add(
        SesionAsistente(
            call_id=llamada.call_id,
            usuario_id=usuario.id,
            rol=usuario.rol,
            creada_en=momento,
            vence_en=momento + VIGENCIA_SESION,
            huella_sesion=huella_sesion(usuario),
        )
    )
    db.session.commit()

    return {
        "access_token": llamada.access_token,
        "call_id": llamada.call_id,
        "transport": llamada.transport,
        "ice_servers": [
            servidor.model_dump(exclude_none=True)
            for servidor in (llamada.ice_servers or [])
        ],
    }


def firma_valida(cuerpo, firma):
    """Verifica X-Retell-Signature sobre el cuerpo crudo de la peticion.

    Usa la verificacion del SDK (la misma funcion que expone `Retell.verify`):
    HMAC-SHA256 con la API key sobre el cuerpo y una marca de tiempo, que
    ademas rechaza firmas de mas de 5 minutos para impedir reenvios.
    """
    from retell.lib.webhook_auth import verify

    api_key = current_app.config.get("RETELL_API_KEY")
    if not api_key or not firma:
        return False
    try:
        return bool(verify(cuerpo, api_key, firma))
    except Exception:
        return False


def motivo_rechazo(sesion):
    """Por que `sesion` ya no autoriza funciones (texto para el usuario), o None."""
    if not sesion.vigente:
        return MOTIVO_VENCIDA
    usuario = sesion.usuario
    if usuario is None or not usuario.activo or usuario.rol != sesion.rol:
        return MOTIVO_INACTIVA
    if sesion.huella_sesion is not None and sesion.huella_sesion != huella_sesion(usuario):
        return MOTIVO_OTRA_SESION
    if acceso.segundos_bloqueo(usuario.correo):
        return MOTIVO_BLOQUEADA
    if usuario.debe_cambiar_contrasena:
        return MOTIVO_CAMBIO_CONTRASENA
    return None


def validar_llamada(call_id):
    """(sesion, motivo): la sesion del asistente si autoriza la peticion.

    Sin sesion para el `call_id`, (None, None). Con una sesion que ya no
    autoriza (ver `motivo_rechazo`), (None, motivo). Que el rol de la sesion
    pueda usar la funcion pedida lo decide el registro de funciones.
    """
    if not call_id or not isinstance(call_id, str):
        return None, None
    sesion = db.session.query(SesionAsistente).filter_by(call_id=call_id).first()
    if sesion is None:
        return None, None
    motivo = motivo_rechazo(sesion)
    return (None, motivo) if motivo else (sesion, None)


def sesion_vigente(call_id):
    """Sesion del asistente para `call_id`, o None si no autoriza la peticion."""
    return validar_llamada(call_id)[0]


def trasladar_llamadas(usuario, huella_anterior):
    """Las llamadas abiertas desde la sesion que se acaba de renovar la siguen.

    Al cambiar la propia contrasena, la sesion que hizo el cambio se renueva
    (`cambiar_contrasena_propia`) y su identificador cambia: sin esto, la
    llamada en curso en esa misma ventana perderia la autorizacion. Las de
    otras sesiones ya no coincidian y siguen sin coincidir. No hace commit.
    """
    db.session.query(SesionAsistente).filter(
        SesionAsistente.usuario_id == usuario.id,
        SesionAsistente.huella_sesion == huella_anterior,
        SesionAsistente.vence_en > ahora(),
    ).update({"huella_sesion": huella_sesion(usuario)}, synchronize_session=False)
