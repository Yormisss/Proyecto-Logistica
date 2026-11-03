"""Avisos salientes a un escenario de Make.

Todo evento se envia como un POST JSON a MAKE_WEBHOOK_URL con un campo `tipo`,
y el escenario de Make lo enruta segun ese campo (salvo `credencial_usuario`,
`restablecimiento_contrasena` y `resumen_diario`, que van cada uno por su
variable al escenario privado):

* `pedido_estado`: un pedido paso a EN_RUTA, ENTREGADO, FALLIDO o CANCELADO.
  Va al cliente, asi que solo se envia si el cliente tiene correo. FALLIDO y
  CANCELADO llevan ademas el motivo.
* `stock_bajo`: un movimiento dejo un producto en o por debajo de su stock
  minimo, o por debajo de cero. Aviso interno: a los gestores logisticos.
* `solicitud_contacto`: un cliente pidio que el gestor lo contacte. A los
  gestores logisticos.
* `accion_asistente`: un registro de la auditoria, con su `id_auditoria`. A
  los administradores. No todo registro lo envia: ver
  `registrar_auditoria(avisar=...)`.
* `ruta_asignada`: al conductor, cuando su ruta de hoy o futura se crea,
  cambia (paradas, recalculo) o se elimina (ver `aviso_ruta`).

Los avisos internos salen uno por destinatario, al correo de avisos de cada
cuenta activa del rol (ver "Destinatarios por rol"); CORREO_OPERACIONES es
solo el respaldo si ninguna tiene correo.
* `credencial_usuario`: la contrasena de una cuenta creada o restablecida por
  voz. Siempre es temporal (`debe_cambiar_contrasena`: true): el usuario la
  reemplaza al iniciar sesion. Va al correo de inicio de sesion del propio
  usuario y es el UNICO lugar por el que viaja esa contrasena: nunca se guarda
  en la auditoria ni en otro evento. Sale por su propio webhook,
  MAKE_WEBHOOK_CREDENCIALES_URL, para que no quede en el historial del
  escenario general; sin el, no se envia.
* `restablecimiento_contrasena`: el enlace de "Olvide mi contrasena", al
  correo de inicio de sesion de quien lo pidio. Quien tiene el enlace cambia
  la contrasena, asi que tambien va por su propio webhook,
  MAKE_WEBHOOK_RESTABLECIMIENTO_URL (ver app/services/recuperacion.py).
* `resumen_diario`: el resumen del dia, a los gestores y administradores, cuando alguien lo
  pide (tablero o voz) o lo dispara el comando programado `enviar-resumen`.
  Va por MAKE_WEBHOOK_RESUMEN_URL y lleva los mismos campos que GET
  /api/automatizacion/resumen-diario (ver app/services/resumen_diario.py).

MAKE_WEBHOOK_CREDENCIALES_URL, MAKE_WEBHOOK_RESTABLECIMIENTO_URL y
MAKE_WEBHOOK_RESUMEN_URL apuntan al webhook del escenario privado ("Rutalytics
Privado", con "Data is confidential"); MAKE_WEBHOOK_URL, al de la operacion
("SGDS"). Todos llevan la misma API key, MAKE_WEBHOOK_KEY.

Todo evento lleva `hora` ("2026-10-13 14:05") y `fecha_hora` en ISO 8601 con
segundos y el desplazamiento de Bogota ("2026-10-13T14:05:09-05:00").

Todo evento lleva quien origino la accion: `realizado_por` (nombre),
`rol_realizado_por` (etiqueta del rol), `origen` ("pantalla", "voz" o
"automático") y `realizado_por_publico` (solo la etiqueta del rol, para los
correos que van a clientes, sin nombres del personal). Las acciones
automaticas usan "Sistema SGDS (automático)".

Tres garantias, iguales para todos los tipos:

* Solo se avisa de lo que quedo confirmado: el evento se encola en la sesion y
  se despacha en `after_commit`; un rollback lo descarta. Asi un cambio que se
  revierte nunca genera un correo.
* El envio va en segundo plano con timeout corto: quien opera no espera a
  Make, y si Make falla o tarda, la operacion ya confirmada no se bloquea ni se
  revierte. El fallo queda solo en el log.
* Sin URL de Make no se envia nada.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import contextmanager

import requests
from flask import current_app, g, has_app_context, has_request_context
from sqlalchemy import event

from app.extensions import db
from app.models import EstadoPedido, Rol, Usuario
from app.tiempo import ahora, iso_bogota

TIPO_PEDIDO_ESTADO = "pedido_estado"
TIPO_STOCK_BAJO = "stock_bajo"
TIPO_SOLICITUD_CONTACTO = "solicitud_contacto"
TIPO_ACCION_ASISTENTE = "accion_asistente"
TIPO_CREDENCIAL_USUARIO = "credencial_usuario"
TIPO_RESTABLECIMIENTO_CONTRASENA = "restablecimiento_contrasena"
TIPO_RESUMEN_DIARIO = "resumen_diario"
TIPO_RUTA_ASIGNADA = "ruta_asignada"

ACCION_RUTA_CREADA = "creada"
ACCION_RUTA_ACTUALIZADA = "actualizada"
ACCION_RUTA_ELIMINADA = "eliminada"

ORIGEN_PANTALLA = "pantalla"
ORIGEN_VOZ = "voz"
ORIGEN_AUTOMATICO = "automático"

# Etiquetas legibles para los eventos (con tildes, a diferencia de las de la UI).
ETIQUETAS_ROL = {
    Rol.CONDUCTOR: "Conductor",
    Rol.DESPACHADOR: "Gestor logístico",
    Rol.ADMIN: "Administrador",
    Rol.CLIENTE: "Cliente",
}
NOMBRE_SISTEMA = "Sistema SGDS (automático)"
ROL_SISTEMA = "Sistema"

ESTADOS_AVISADOS = (
    EstadoPedido.EN_RUTA, EstadoPedido.ENTREGADO, EstadoPedido.FALLIDO, EstadoPedido.CANCELADO,
)

# (conexion, respuesta) en segundos. Make responde "Accepted" de inmediato; si
# tarda mas, el aviso se pierde antes que retener un hilo.
TIMEOUT_MAKE = (3, 5)

_CLAVE_PENDIENTES = "avisos_make_pendientes"

# Pocos hilos: los avisos son esporadicos (uno por cambio de estado) y un
# "Iniciar ruta" con muchas paradas solo forma una cola corta.
_ejecutor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="aviso-make")
_en_curso = set()

registro = logging.getLogger(__name__)


def _marca(momento=None):
    """{"hora": "2026-10-13 14:05", "fecha_hora": "2026-10-13T14:05:09-05:00"}."""
    momento = momento or ahora()
    return {"hora": momento.strftime("%Y-%m-%d %H:%M"), "fecha_hora": iso_bogota(momento)}


def credenciales_configuradas():
    """Si hay webhook para enviar contrasenas (sin el, no se crean cuentas por voz)."""
    return bool((current_app.config.get("MAKE_WEBHOOK_CREDENCIALES_URL") or "").strip())


def origen_actual():
    """"voz" dentro de una funcion del asistente, "pantalla" en otra peticion web,
    None fuera de una peticion (un comando o un proceso automatico)."""
    if not has_request_context():
        return None
    return g.get("origen_accion") or ORIGEN_PANTALLA


def usuario_actual():
    if not has_request_context():
        return None
    if g.get("usuario") is not None:          # funcion del asistente de voz
        return g.usuario
    from flask_login import current_user

    return current_user._get_current_object() if current_user.is_authenticated else None


def etiqueta_rol(rol):
    return ETIQUETAS_ROL.get(rol, rol)


def datos_actor(usuario=None, origen=None):
    """Quien origino la accion, para todo evento.

    Sin usuario (ni explicito ni en la peticion) es el sistema, automatico.
    """
    usuario = usuario if usuario is not None else usuario_actual()
    if usuario is None or origen == ORIGEN_AUTOMATICO:
        return {"realizado_por": NOMBRE_SISTEMA, "rol_realizado_por": ROL_SISTEMA,
                "origen": ORIGEN_AUTOMATICO, "realizado_por_publico": ROL_SISTEMA}
    etiqueta = etiqueta_rol(usuario.rol)
    return {"realizado_por": usuario.nombre, "rol_realizado_por": etiqueta,
            "origen": origen or origen_actual() or ORIGEN_PANTALLA,
            "realizado_por_publico": etiqueta}


def _usuario(usuario_id):
    return db.session.get(Usuario, usuario_id) if usuario_id else None


# --------------------------------------------------------------------------
# Destinatarios por rol
# --------------------------------------------------------------------------
# Los avisos internos llegan a las cuentas activas del rol que los atiende, a su
# `correo_para_avisos` (el de avisos o, sin el, el de inicio de sesion): sale un
# evento por destinatario, cada uno con su `correo_destino`, y los escenarios de
# Make no cambian. CORREO_OPERACIONES queda de respaldo, solo si ninguna cuenta
# de esos roles tiene correo.
DESTINATARIOS_STOCK = (Rol.DESPACHADOR,)
DESTINATARIOS_SOLICITUD = (Rol.DESPACHADOR,)
DESTINATARIOS_RESUMEN = (Rol.DESPACHADOR, Rol.ADMIN)
DESTINATARIOS_ACCION = (Rol.ADMIN,)


def correos_de_rol(roles):
    """Correos de avisos de las cuentas activas de `roles`, sin repetir; o el de respaldo.

    Lista vacia si no hay ninguno, ni CORREO_OPERACIONES: entonces el aviso no sale.
    """
    cuentas = (db.session.query(Usuario)
               .filter(Usuario.rol.in_(list(roles)), Usuario.activo.is_(True))
               .order_by(Usuario.rol, Usuario.nombre).all())
    correos = list(dict.fromkeys(c.correo_para_avisos.strip().lower() for c in cuentas
                                 if (c.correo_para_avisos or "").strip()))
    if correos:
        return correos
    respaldo = (current_app.config.get("CORREO_OPERACIONES") or "").strip()
    return [respaldo] if respaldo else []


def encolar_por_destinatario(tipo, datos, roles, actor=None, clave_url="MAKE_WEBHOOK_URL"):
    """Un evento por cada correo de `roles` (ver `correos_de_rol`). Devuelve los correos."""
    correos = correos_de_rol(roles)
    for correo in correos:
        encolar_evento(tipo, {**datos, "correo_destino": correo}, actor, clave_url=clave_url)
    return correos


@contextmanager
def sin_avisos():
    """Dentro del bloque no se encola ningun evento (la siembra de datos de demostracion)."""
    anterior = g.get("_sin_avisos", False)
    g._sin_avisos = True
    try:
        yield
    finally:
        g._sin_avisos = anterior


def encolar_evento(tipo, datos, actor=None, clave_url="MAKE_WEBHOOK_URL"):
    """Encola un evento de `tipo` para Make; sale tras el commit.

    La URL y la API key se leen aqui, con el contexto de la aplicacion: el hilo
    que envia no lo tiene. `actor` es el resultado de `datos_actor`; si no
    viene, se toma de la peticion en curso. `clave_url` es la variable con el
    webhook de destino.
    """
    if has_app_context() and g.get("_sin_avisos"):
        return
    config = current_app.config
    url = (config.get(clave_url) or "").strip()
    if not url:
        return
    cabeceras = {}
    if config.get("MAKE_WEBHOOK_KEY"):
        cabeceras["x-make-apikey"] = config["MAKE_WEBHOOK_KEY"]
    db.session.info.setdefault(_CLAVE_PENDIENTES, []).append(
        (url, cabeceras, {"tipo": tipo, **datos, **(actor or datos_actor())})
    )


def aviso_estado_pedido(pedido, motivo=None, usuario_id=None):
    """Avisa al cliente del estado actual del pedido.

    En CANCELADO y FALLIDO el aviso lleva ademas `motivo`: el de la anulacion,
    o el de la prueba de entrega (motivo_fallo) del intento fallido.
    """
    if pedido.estado not in ESTADOS_AVISADOS:
        return
    correo = ((pedido.cliente.correo if pedido.cliente else None) or "").strip()
    if not correo:
        return

    datos = {
        "codigo": pedido.codigo,
        "estado": pedido.estado,
        "cliente": pedido.cliente_nombre,
        "correo": correo,
        "direccion": pedido.direccion_completa,
        "ventana": pedido.ventana_texto,
        **_marca(),
    }
    if pedido.estado in (EstadoPedido.CANCELADO, EstadoPedido.FALLIDO):
        datos["motivo"] = motivo
    # El correo al cliente debe usar `realizado_por_publico` (solo el rol).
    encolar_evento(TIPO_PEDIDO_ESTADO, datos, datos_actor(_usuario(usuario_id)))


def aviso_stock(producto, stock_previo, pedido=None, usuario_id=None):
    """Avisa a operaciones si el movimiento que acaba de aplicarse cruzo un umbral.

    Hay dos umbrales y cada uno avisa una sola vez, al cruzarlo hacia abajo: el
    stock minimo (previo > minimo >= nuevo) y el cero (previo >= 0 > nuevo, un
    descuadre entre el inventario registrado y el fisico). Los movimientos
    posteriores que siguen por debajo no repiten el aviso. Si un mismo
    movimiento cruza ambos, sale un solo aviso con negativo=true.

    `pedido` es el codigo del pedido cuya entrega desconto el stock, o None en
    un movimiento manual. `usuario_id` es quien hizo el movimiento.
    """
    nuevo = producto.stock_actual
    cruza_minimo = stock_previo > producto.stock_minimo >= nuevo
    cruza_cero = stock_previo >= 0 > nuevo
    if not (cruza_minimo or cruza_cero) or not producto.activo:
        return
    encolar_por_destinatario(TIPO_STOCK_BAJO, {
        "sku": producto.sku,
        "producto": producto.nombre,
        "stock_actual": nuevo,
        "stock_minimo": producto.stock_minimo,
        "negativo": nuevo < 0,
        "pedido": pedido,
        **_marca(),
    }, DESTINATARIOS_STOCK, datos_actor(_usuario(usuario_id)))


def aviso_solicitud_contacto(solicitud):
    """Avisa a los gestores logisticos que un cliente pidio contacto."""
    encolar_por_destinatario(TIPO_SOLICITUD_CONTACTO, {
        "cliente": solicitud.cliente.nombre,
        "correo": solicitud.correo,
        "telefono": solicitud.telefono,
        "motivo": solicitud.motivo,
        **_marca(),
    }, DESTINATARIOS_SOLICITUD, datos_actor(solicitud.usuario))


def aviso_accion_asistente(registro):
    """Envia a los administradores un registro de la auditoria (nunca lleva contrasenas)."""
    encolar_por_destinatario(TIPO_ACCION_ASISTENTE, {
        "id_auditoria": registro.id,
        "usuario": registro.usuario_nombre,
        "rol": etiqueta_rol(registro.rol) if registro.rol else ROL_SISTEMA,
        "accion": registro.accion,
        "entidad": registro.entidad,
        "resumen": registro.resumen,
        "detalle": registro.detalle_legible,
        "resultado": registro.resultado,
        **_marca(registro.registrado_en),
    }, DESTINATARIOS_ACCION, datos_actor(registro.usuario, registro.origen))


def aviso_ruta(ruta, accion, detalle, paradas, usuario_id=None):
    """Avisa al conductor que su ruta se creo, cambio o se elimino.

    Solo rutas de hoy o de una fecha futura: cambiar una ruta pasada no le
    sirve al conductor. Un evento por operacion, al correo de avisos del
    conductor. `paradas` son las que tiene la ruta despues del cambio (0 si se
    elimino) y `detalle` lo cuenta en una frase ("Se agregó la parada de ...").
    """
    from app.tiempo import hoy

    conductor = ruta.conductor
    if conductor is None or ruta.fecha < hoy() or not (conductor.correo_para_avisos or "").strip():
        return
    encolar_evento(TIPO_RUTA_ASIGNADA, {
        "conductor": conductor.nombre,
        "correo_destino": conductor.correo_para_avisos.strip().lower(),
        "ruta": ruta.codigo,
        "fecha": ruta.fecha.strftime("%d/%m/%Y"),
        "paradas": paradas,
        "accion": accion,
        "detalle_ruta": detalle,
        **_marca(),
    }, datos_actor(_usuario(usuario_id)))


ACCION_CREACION = "creación"
ACCION_RESTABLECIMIENTO = "restablecimiento"
INDICACION_TEMPORAL = ("Esta contraseña es temporal: al iniciar sesión el sistema le pedirá cambiarla "
                       "por una propia antes de continuar.")


def aviso_credencial_usuario(usuario, contrasena, accion, creado_por):
    """Envia la contrasena de una cuenta al correo de inicio de sesion del usuario.

    Solo para las cuentas creadas o restablecidas por voz. Es el unico lugar por
    el que viaja la contrasena: no se guarda en la auditoria ni en otro evento.
    `accion` es ACCION_CREACION o ACCION_RESTABLECIMIENTO.
    """
    encolar_evento(TIPO_CREDENCIAL_USUARIO, {
        "nombre": usuario.nombre,
        "correo": usuario.correo,
        "contrasena": contrasena,
        # La generada por voz siempre es temporal (ver services/usuarios.py).
        "debe_cambiar_contrasena": bool(usuario.debe_cambiar_contrasena),
        "indicaciones": INDICACION_TEMPORAL if usuario.debe_cambiar_contrasena else "",
        "url_acceso": (current_app.config.get("URL_PUBLICA") or "").strip().rstrip("/"),
        "creado_por": f"{creado_por.nombre} ({etiqueta_rol(creado_por.rol)})",
        "accion": accion,
        **_marca(),
        "correo_destino": usuario.correo,
    }, datos_actor(creado_por), clave_url="MAKE_WEBHOOK_CREDENCIALES_URL")


def restablecimiento_configurado():
    """Si hay webhook para enviar el enlace de "Olvide mi contrasena"."""
    return bool((current_app.config.get("MAKE_WEBHOOK_RESTABLECIMIENTO_URL") or "").strip())


def aviso_restablecimiento(usuario, enlace, minutos):
    """Envia al usuario el enlace para restablecer su contrasena.

    Como `credencial_usuario`, el enlace viaja solo aqui: no se guarda en la
    auditoria ni en otro evento. Lo pidio la propia persona, que es el actor.
    """
    encolar_evento(TIPO_RESTABLECIMIENTO_CONTRASENA, {
        "nombre": usuario.nombre,
        "correo": usuario.correo,
        "enlace": enlace,
        "vence_en_minutos": minutos,
        **_marca(),
        "correo_destino": usuario.correo,
    }, datos_actor(usuario), clave_url="MAKE_WEBHOOK_RESTABLECIMIENTO_URL")


def _enviar(url, cabeceras, datos):
    try:
        respuesta = requests.post(url, json=datos, headers=cabeceras, timeout=TIMEOUT_MAKE)
        respuesta.raise_for_status()
    except Exception as error:
        registro.warning(
            "No se pudo enviar a Make el aviso %s (%s): %s",
            datos.get("tipo"),
            datos.get("codigo") or datos.get("sku") or datos.get("cliente") or datos.get("accion"), error,
        )


def _despachar(sesion):
    for url, cabeceras, datos in sesion.info.pop(_CLAVE_PENDIENTES, []):
        futuro = _ejecutor.submit(_enviar, url, cabeceras, datos)
        _en_curso.add(futuro)
        futuro.add_done_callback(_en_curso.discard)


def _descartar(sesion):
    sesion.info.pop(_CLAVE_PENDIENTES, None)


def esperar_envios(segundos=10):
    """Espera a que terminen los avisos en curso. Lo usan las pruebas."""
    wait(list(_en_curso), timeout=segundos)


def registrar_avisos():
    """Engancha el despacho y el descarte de avisos al ciclo de la sesion."""
    fabrica = db.session.session_factory
    if not event.contains(fabrica, "after_commit", _despachar):
        event.listen(fabrica, "after_commit", _despachar)
        event.listen(fabrica, "after_rollback", _descartar)
