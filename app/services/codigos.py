"""Generacion de consecutivos legibles para pedidos y rutas."""

from sqlalchemy import func

from app.extensions import db
from app.tiempo import hoy


def _siguiente_consecutivo(modelo, prefijo, fecha):
    """Devuelve el siguiente numero disponible para el prefijo y la fecha dados."""
    patron = f"{prefijo}-{fecha.strftime('%Y%m%d')}-%"
    ultimo = (
        db.session.query(func.max(modelo.codigo))
        .filter(modelo.codigo.like(patron))
        .scalar()
    )
    if not ultimo:
        return 1
    try:
        return int(ultimo.rsplit("-", 1)[1]) + 1
    except (IndexError, ValueError):
        return 1


def generar_codigo_pedido(fecha=None):
    from app.models import Pedido

    fecha = fecha or hoy()
    numero = _siguiente_consecutivo(Pedido, "PED", fecha)
    return f"PED-{fecha.strftime('%Y%m%d')}-{numero:03d}"


def generar_codigo_ruta(fecha=None):
    from app.models import Ruta

    fecha = fecha or hoy()
    numero = _siguiente_consecutivo(Ruta, "RUT", fecha)
    return f"RUT-{fecha.strftime('%Y%m%d')}-{numero:02d}"
