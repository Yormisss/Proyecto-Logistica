"""Integracion con Retell AI para el asistente de voz.

Concentra todo lo que toca a Retell: que agente atiende a cada rol, la creacion
de la llamada web, la verificacion de la firma de las custom functions y la
resolucion del `call_id` al usuario que abrio la llamada. La API key se usa
solo aqui, del lado del servidor.
"""

from datetime import timedelta

from flask import current_app

from app.extensions import db
from app.models import VIGENCIA_SESION, Rol, SesionAsistente
from app.tiempo import ahora

# Rol -> clave de configuracion con el agent_id de Retell que lo atiende. Un rol
# fuera del mapa (o con la clave vacia) no tiene asistente.
AGENTES_POR_ROL = {
    Rol.CONDUCTOR: "RETELL_AGENTE_CONDUCTOR_ID",
}

# Las sesiones vencidas se conservan un dia para poder auditar llamadas
# recientes; despues se purgan al crear la siguiente.
RETENCION_SESIONES = timedelta(days=1)


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


def cliente_retell():
    """Cliente del SDK de Retell. Las pruebas lo reemplazan por uno simulado.

    Sin reintentos y con timeout corto: el conductor esta esperando frente al
    boton, y un reintento silencioso solo alarga la espera sin informarle.
    """
    from retell import Retell

    return Retell(
        api_key=current_app.config["RETELL_API_KEY"],
        max_retries=0,
        timeout=10,
    )


def iniciar_llamada(usuario):
    """Crea la llamada web para `usuario` y registra su sesion del asistente.

    Devuelve los datos que el navegador necesita para unirse a la llamada. La
    respuesta de /v3/create-web-call usa el transporte "gateway", que exige el
    call_id y los servidores ICE ademas del access_token; ninguno de los
    cuatro da acceso a la cuenta de Retell.
    """
    agent_id = agente_para(usuario.rol)
    if agent_id is None:
        raise AsistenteNoDisponible(usuario.rol)

    try:
        llamada = cliente_retell().call.create_web_call(
            agent_id=agent_id,
            retell_llm_dynamic_variables={"nombre_usuario": usuario.nombre},
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


def sesion_vigente(call_id, rol):
    """Sesion del asistente para `call_id`, o None si no autoriza la peticion.

    Rechaza un call_id desconocido, una sesion vencida, una abierta con otro
    rol o la de un usuario desactivado despues de iniciar la llamada.
    """
    if not call_id or not isinstance(call_id, str):
        return None
    sesion = db.session.query(SesionAsistente).filter_by(call_id=call_id).first()
    if sesion is None or not sesion.vigente or sesion.rol != rol:
        return None
    usuario = sesion.usuario
    if usuario is None or not usuario.activo or usuario.rol != rol:
        return None
    return sesion
