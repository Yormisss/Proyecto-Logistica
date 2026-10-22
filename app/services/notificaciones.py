"""Avisos salientes a un escenario de Make.

Todo evento se envia como un POST JSON a MAKE_WEBHOOK_URL con un campo `tipo`,
y el escenario de Make lo enruta segun ese campo (salvo `credencial_usuario`,
que va solo a MAKE_WEBHOOK_CREDENCIALES_URL):

* `pedido_estado`: un pedido paso a EN_RUTA, ENTREGADO, FALLIDO o CANCELADO.
  Va al cliente, asi que solo se envia si el cliente tiene correo. FALLIDO y
  CANCELADO llevan ademas el motivo.
* `stock_bajo`: un movimiento dejo un producto en o por debajo de su stock
  minimo, o por debajo de cero. Es un aviso interno: va a CORREO_OPERACIONES y
  sin esa variable no se envia.
* `solicitud_contacto`: un cliente pidio que el gestor lo contacte. Tambien es
  interno, con la misma regla.
* `accion_asistente`: un registro de la auditoria, con su `id_auditoria`.
  Interno. No todo registro lo envia: ver `registrar_auditoria(avisar=...)`.
* `credencial_usuario`: la contrasena de una cuenta creada o restablecida por
  voz. Va al correo de inicio de sesion del propio usuario y es el UNICO lugar
  por el que viaja esa contrasena: nunca se guarda en la auditoria ni en otro
  evento. Sale por su propio webhook, MAKE_WEBHOOK_CREDENCIALES_URL, para que
  no quede en el historial del escenario general; sin el, no se envia.

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

import requests
from flask import current_app, g, has_request_context
from sqlalchemy import event

from app.extensions import db
from app.models import EstadoPedido, Rol, Usuario
from app.tiempo import ahora, iso_bogota

TIPO_PEDIDO_ESTADO = "pedido_estado"
TIPO_STOCK_BAJO = "stock_bajo"
TIPO_SOLICITUD_CONTACTO = "solicitud_contacto"
TIPO_ACCION_ASISTENTE = "accion_asistente"
TIPO_CREDENCIAL_USUARIO = "credencial_usuario"

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


def encolar_evento(tipo, datos, actor=None, clave_url="MAKE_WEBHOOK_URL"):
    """Encola un evento de `tipo` para Make; sale tras el commit.

    La URL y la API key se leen aqui, con el contexto de la aplicacion: el hilo
    que envia no lo tiene. `actor` es el resultado de `datos_actor`; si no
    viene, se toma de la peticion en curso. `clave_url` es la variable con el
    webhook de destino.
    """
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
    correo = (current_app.config.get("CORREO_OPERACIONES") or "").strip()
    if not correo:
        return

    encolar_evento(TIPO_STOCK_BAJO, {
        "sku": producto.sku,
        "producto": producto.nombre,
        "stock_actual": nuevo,
        "stock_minimo": producto.stock_minimo,
        "negativo": nuevo < 0,
        "pedido": pedido,
        "correo_destino": correo,
        **_marca(),
    }, datos_actor(_usuario(usuario_id)))


def aviso_solicitud_contacto(solicitud):
    """Avisa a operaciones que un cliente pidio contacto con el gestor."""
    correo_destino = (current_app.config.get("CORREO_OPERACIONES") or "").strip()
    if not correo_destino:
        return
    encolar_evento(TIPO_SOLICITUD_CONTACTO, {
        "cliente": solicitud.cliente.nombre,
        "correo": solicitud.correo,
        "telefono": solicitud.telefono,
        "motivo": solicitud.motivo,
        **_marca(),
        "correo_destino": correo_destino,
    }, datos_actor(solicitud.usuario))


def aviso_accion_asistente(registro):
    """Envia a operaciones un registro de la auditoria (nunca lleva contrasenas)."""
    correo_destino = (current_app.config.get("CORREO_OPERACIONES") or "").strip()
    if not correo_destino:
        return
    encolar_evento(TIPO_ACCION_ASISTENTE, {
        "id_auditoria": registro.id,
        "usuario": registro.usuario_nombre,
        "rol": etiqueta_rol(registro.rol) if registro.rol else ROL_SISTEMA,
        "accion": registro.accion,
        "entidad": registro.entidad,
        "resumen": registro.resumen,
        "detalle": registro.detalle_legible,
        "resultado": registro.resultado,
        **_marca(registro.registrado_en),
        "correo_destino": correo_destino,
    }, datos_actor(registro.usuario, registro.origen))


ACCION_CREACION = "creación"
ACCION_RESTABLECIMIENTO = "restablecimiento"


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
        "url_acceso": (current_app.config.get("URL_PUBLICA") or "").strip().rstrip("/"),
        "creado_por": f"{creado_por.nombre} ({etiqueta_rol(creado_por.rol)})",
        "accion": accion,
        **_marca(),
        "correo_destino": usuario.correo,
    }, datos_actor(creado_por), clave_url="MAKE_WEBHOOK_CREDENCIALES_URL")


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
