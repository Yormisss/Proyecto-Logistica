"""Avisos al cliente por correo a traves de un escenario de Make.

Cuando un pedido pasa a EN_RUTA, ENTREGADO o FALLIDO se envia un POST con los
datos del pedido a MAKE_WEBHOOK_URL, y el escenario de Make redacta y envia el
correo. Tres garantias:

* Solo se avisa de lo que quedo confirmado: `cambiar_estado` encola el aviso en
  la sesion y se despacha en `after_commit`; un rollback lo descarta. Asi un
  cambio que se revierte nunca genera un correo.
* El envio va en segundo plano con timeout corto: el conductor no espera a
  Make, y si Make falla o tarda, la operacion ya confirmada no se bloquea ni se
  revierte. El fallo queda solo en el log.
* Sin correo del cliente (o sin URL de Make) no se envia nada.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, wait

import requests
from flask import current_app
from sqlalchemy import event

from app.extensions import db
from app.models import EstadoPedido
from app.tiempo import ahora

ESTADOS_AVISADOS = (EstadoPedido.EN_RUTA, EstadoPedido.ENTREGADO, EstadoPedido.FALLIDO)

# (conexion, respuesta) en segundos. Make responde "Accepted" de inmediato; si
# tarda mas, el aviso se pierde antes que retener un hilo.
TIMEOUT_MAKE = (3, 5)

_CLAVE_PENDIENTES = "avisos_make_pendientes"

# Pocos hilos: los avisos son esporadicos (uno por cambio de estado) y un
# "Iniciar ruta" con muchas paradas solo forma una cola corta.
_ejecutor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="aviso-make")
_en_curso = set()

registro = logging.getLogger(__name__)


def encolar_aviso(pedido):
    """Prepara el aviso del estado actual del pedido; sale tras el commit."""
    if pedido.estado not in ESTADOS_AVISADOS:
        return
    url = current_app.config.get("MAKE_WEBHOOK_URL")
    if not url:
        return
    correo = ((pedido.cliente.correo if pedido.cliente else None) or "").strip()
    if not correo:
        return

    direccion = f"{pedido.direccion}, {pedido.ciudad}" if pedido.ciudad else pedido.direccion
    datos = {
        "codigo": pedido.codigo,
        "estado": pedido.estado,
        "cliente": pedido.cliente_nombre,
        "correo": correo,
        "direccion": direccion,
        "ventana": pedido.ventana_texto,
        "hora": ahora().strftime("%Y-%m-%d %H:%M"),
    }
    db.session.info.setdefault(_CLAVE_PENDIENTES, []).append((url, datos))


def _enviar(url, datos):
    try:
        respuesta = requests.post(url, json=datos, timeout=TIMEOUT_MAKE)
        respuesta.raise_for_status()
    except Exception as error:
        registro.warning(
            "No se pudo avisar a Make del pedido %s (%s): %s",
            datos.get("codigo"), datos.get("estado"), error,
        )


def _despachar(sesion):
    for url, datos in sesion.info.pop(_CLAVE_PENDIENTES, []):
        futuro = _ejecutor.submit(_enviar, url, datos)
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
