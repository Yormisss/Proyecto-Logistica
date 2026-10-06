"""Movimientos de inventario (RF5).

Punto unico por el que cambia el stock fuera de una entrega: lo usan la
pantalla de inventario y el asistente de voz, para que ambos dejen la misma
trazabilidad y disparen el mismo aviso de stock bajo.
"""

from app.extensions import db
from app.models import MovimientoInventario, Producto, TipoMovimiento
from app.services.notificaciones import aviso_stock


def registrar_movimiento(producto, tipo, cantidad, usuario_id, motivo=None, pedido_id=None):
    """Aplica un movimiento y deja la trazabilidad correspondiente.

    `cantidad` siempre es positiva; el signo lo determina el tipo de movimiento.

    Vuelve a leer el producto con `with_for_update()` justo antes de tocar el
    stock: sin el bloqueo, dos ajustes manuales concurrentes sobre el mismo
    producto (o un ajuste que coincide con un despacho en curso) podrian
    partir del mismo `stock_actual` y la segunda escritura pisaria la
    primera (actualizacion perdida). `populate_existing()` fuerza a refrescar
    sus columnas desde esa fila aunque el producto ya estuviera cargado en el
    identity map de la sesion.
    """
    producto = (
        db.session.query(Producto)
        .filter_by(id=producto.id)
        .populate_existing()
        .with_for_update()
        .one()
    )
    stock_previo = producto.stock_actual

    if tipo in (TipoMovimiento.ENTRADA, TipoMovimiento.REVERSION):
        producto.stock_actual += cantidad
    elif tipo == TipoMovimiento.SALIDA:
        producto.stock_actual -= cantidad
    elif tipo == TipoMovimiento.AJUSTE:
        producto.stock_actual = cantidad
    else:
        raise ValueError(f"Tipo de movimiento desconocido: {tipo}")

    movimiento = MovimientoInventario(
        producto_id=producto.id,
        pedido_id=pedido_id,
        usuario_id=usuario_id,
        tipo=tipo,
        cantidad=cantidad,
        stock_resultante=producto.stock_actual,
        motivo=motivo,
    )
    db.session.add(movimiento)
    aviso_stock(producto, stock_previo)
    return movimiento
