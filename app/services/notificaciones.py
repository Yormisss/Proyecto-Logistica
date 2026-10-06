"""Avisos salientes a un escenario de Make.

Todo evento se envia como un POST JSON a MAKE_WEBHOOK_URL con un campo `tipo`,
y el escenario de Make lo enruta segun ese campo:

* `pedido_estado`: un pedido paso a EN_RUTA, ENTREGADO, FALLIDO o CANCELADO.
  Va al cliente, asi que solo se envia si el cliente tiene correo. FALLIDO y
  CANCELADO llevan ademas el motivo.
* `stock_bajo`: un movimiento dejo un producto en o por debajo de su stock
  minimo, o por debajo de cero. Es un aviso interno: va a CORREO_OPERACIONES y
  sin esa variable no se envia.
* `solicitud_contacto`: un cliente pidio que el gestor lo contacte. Tambien es
  interno, con la misma regla.

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
from flask import current_app
from sqlalchemy import event

from app.extensions import db
from app.models import EstadoPedido
from app.tiempo import ahora

TIPO_PEDIDO_ESTADO = "pedido_estado"
TIPO_STOCK_BAJO = "stock_bajo"
TIPO_SOLICITUD_CONTACTO = "solicitud_contacto"

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


def _hora():
    return ahora().strftime("%Y-%m-%d %H:%M")


def encolar_evento(tipo, datos):
    """Encola un evento de `tipo` para Make; sale tras el commit.

    La URL y la API key se leen aqui, con el contexto de la aplicacion: el hilo
    que envia no lo tiene.
    """
    config = current_app.config
    url = config.get("MAKE_WEBHOOK_URL")
    if not url:
        return
    cabeceras = {}
    if config.get("MAKE_WEBHOOK_KEY"):
        cabeceras["x-make-apikey"] = config["MAKE_WEBHOOK_KEY"]
    db.session.info.setdefault(_CLAVE_PENDIENTES, []).append(
        (url, cabeceras, {"tipo": tipo, **datos})
    )


def aviso_estado_pedido(pedido, motivo=None):
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
        "hora": _hora(),
    }
    if pedido.estado in (EstadoPedido.CANCELADO, EstadoPedido.FALLIDO):
        datos["motivo"] = motivo
    encolar_evento(TIPO_PEDIDO_ESTADO, datos)


def aviso_stock(producto, stock_previo, pedido=None):
    """Avisa a operaciones si el movimiento que acaba de aplicarse cruzo un umbral.

    Hay dos umbrales y cada uno avisa una sola vez, al cruzarlo hacia abajo: el
    stock minimo (previo > minimo >= nuevo) y el cero (previo >= 0 > nuevo, un
    descuadre entre el inventario registrado y el fisico). Los movimientos
    posteriores que siguen por debajo no repiten el aviso. Si un mismo
    movimiento cruza ambos, sale un solo aviso con negativo=true.

    `pedido` es el codigo del pedido cuya entrega desconto el stock, o None en
    un movimiento manual.
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
        "hora": _hora(),
    })


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
        "hora": _hora(),
        "correo_destino": correo_destino,
    })


def _enviar(url, cabeceras, datos):
    try:
        respuesta = requests.post(url, json=datos, headers=cabeceras, timeout=TIMEOUT_MAKE)
        respuesta.raise_for_status()
    except Exception as error:
        registro.warning(
            "No se pudo enviar a Make el aviso %s (%s): %s",
            datos.get("tipo"), datos.get("codigo") or datos.get("sku") or datos.get("cliente"), error,
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
