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
    aviso_stock(producto, stock_previo, usuario_id=usuario_id)
    return movimiento


# --------------------------------------------------------------------------
# Alta y edicion de productos (pantalla y asistente de voz)
# --------------------------------------------------------------------------

from app.services.validacion import DatosInvalidos, Validador  # noqa: E402

CAMPOS_PRODUCTO = ("sku", "nombre", "descripcion", "unidad", "stock_minimo", "activo")


def _booleano(valor, defecto=True):
    if valor is None or valor == "":
        return defecto
    if isinstance(valor, str):
        return valor.strip().lower() in ("1", "true", "si", "sí", "on", "y", "yes")
    return bool(valor)


def tiene_movimientos(producto):
    return producto.id is not None and producto.movimientos.first() is not None


def sku_fijo(producto):
    """El SKU solo se edita mientras el producto no tenga movimientos de inventario."""
    return producto is not None and tiene_movimientos(producto)


def validar_producto(datos, producto=None):
    """Datos normalizados de un producto; lanza DatosInvalidos si no cumplen."""
    actuales = {campo: getattr(producto, campo) for campo in CAMPOS_PRODUCTO} if producto else {}
    datos = {**actuales, **datos}
    v = Validador()
    sku = v.texto(datos, "sku", "el SKU", 40, requerido=True)
    sku = sku.upper() if sku else sku
    nombre = v.texto(datos, "nombre", "el nombre del producto", 160, requerido=True)
    descripcion = v.texto(datos, "descripcion", "la descripción", 255)
    unidad = v.texto(datos, "unidad", "la unidad", 20, defecto="UND")
    stock_minimo = v.entero(datos, "stock_minimo", "el stock mínimo", minimo=0, defecto=0)
    activo = _booleano(datos.get("activo"))

    if sku:
        if producto is not None and sku != producto.sku and sku_fijo(producto):
            v.invalido("sku", "El SKU queda fijo porque el producto ya tiene movimientos de inventario.")
            sku = producto.sku
        choque = db.session.query(Producto).filter(Producto.sku == sku)
        if producto is not None:
            choque = choque.filter(Producto.id != producto.id)
        if choque.first():
            v.invalido("sku", "Ya existe un producto con este SKU." if producto is None
                       else "Ya existe otro producto con este SKU.")
    v.lanzar_si_hay()
    return {"sku": sku, "nombre": nombre, "descripcion": descripcion, "unidad": unidad,
            "stock_minimo": stock_minimo, "activo": activo}


def validar_stock_inicial(datos):
    v = Validador()
    stock = v.entero(datos, "stock_inicial", "el stock inicial", minimo=0, defecto=0)
    v.lanzar_si_hay()
    return stock


def crear_producto(datos, usuario_id, motivo_stock="Stock inicial"):
    """Crea el producto en cero; el stock inicial entra como ENTRADA.

    Asi todo el stock queda respaldado por su trazabilidad. Con stock inicial,
    el producto nace con un movimiento y su SKU queda fijo.
    """
    limpios = validar_producto(datos)
    stock_inicial = validar_stock_inicial(datos)
    producto = Producto(stock_actual=0, **limpios)
    db.session.add(producto)
    db.session.flush()
    if stock_inicial:
        registrar_movimiento(producto, TipoMovimiento.ENTRADA, stock_inicial, usuario_id,
                             motivo=motivo_stock)
    return producto


def editar_producto(producto, datos, usuario_id, motivo_ajuste="Ajuste desde edicion del producto"):
    """Aplica los cambios y devuelve {campo: (antes, despues)}.

    Si `datos` trae `stock_actual` distinto, lo registra como AJUSTE: el stock
    solo cambia mediante movimientos.
    """
    antes = {campo: getattr(producto, campo) for campo in CAMPOS_PRODUCTO}
    limpios = validar_producto(datos, producto)
    stock_nuevo = None
    if datos.get("stock_actual") not in (None, ""):
        v = Validador()
        stock_nuevo = v.entero(datos, "stock_actual", "el stock actual", minimo=0)
        v.lanzar_si_hay()
    cambios = {campo: (antes[campo], valor) for campo, valor in limpios.items() if antes[campo] != valor}
    for campo, valor in limpios.items():
        setattr(producto, campo, valor)
    if stock_nuevo is not None and stock_nuevo != producto.stock_actual:
        cambios["stock_actual"] = (producto.stock_actual, stock_nuevo)
        registrar_movimiento(producto, TipoMovimiento.AJUSTE, stock_nuevo, usuario_id, motivo=motivo_ajuste)
    return cambios


def cambiar_estado_producto(producto, activo):
    if producto.activo == activo:
        raise DatosInvalidos([(None, f"El producto {producto.sku} ya está "
                                     f"{'activo' if activo else 'inactivo'}.")])
    producto.activo = activo
