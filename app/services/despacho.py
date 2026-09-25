"""RF4 - Actualizacion de Estados en Terreno y RF5 - Sincronizacion Logica de Inventario.

Concentra la regla de negocio central del proyecto: el inventario del almacen se
descuenta unica y exclusivamente cuando el conductor confirma la entrega. Esto
conecta la ultima milla con la bodega y elimina la descoordinacion entre el stock
real y las ordenes de despacho descrita en la cadena causal 2.3.2.
"""

from app.extensions import db
from app.models import (
    EstadoPedido,
    EstadoRuta,
    EventoPedido,
    MovimientoInventario,
    Pedido,
    Producto,
    PruebaEntrega,
    TipoMovimiento,
)
from app.tiempo import ahora


class TransicionInvalida(Exception):
    """El cambio de estado solicitado no esta permitido para este pedido."""


class ResultadoTransicion:
    def __init__(self, pedido, estado_anterior, movimientos=None, advertencias=None):
        self.pedido = pedido
        self.estado_anterior = estado_anterior
        self.movimientos = movimientos or []
        self.advertencias = advertencias or []


def _descontar_inventario(pedido, usuario_id):
    """RF5 - Descuenta del inventario general los productos del pedido entregado.

    La bandera `inventario_descontado` garantiza idempotencia: aunque la peticion
    se repita (reintento del conductor por conectividad intermitente), el stock
    se afecta una sola vez.

    Cada producto se vuelve a leer con `with_for_update()` justo antes de
    modificarlo: sin el bloqueo, dos peticiones concurrentes que despachan el
    mismo producto podrian leer el mismo `stock_actual`, calcular el
    descuento por separado y la segunda escritura pisaria la primera
    (actualizacion perdida). Con la fila bloqueada, la segunda peticion espera
    a que la primera confirme y parte del stock ya actualizado. `populate_existing()`
    es necesario ademas del bloqueo: si el producto ya estaba cargado en el
    identity map de esta sesion, una consulta nueva por si sola devuelve el
    mismo objeto sin refrescar sus columnas, y se seguiria calculando sobre el
    valor que ya tenia en memoria Python.
    """
    if pedido.inventario_descontado:
        return [], []

    movimientos, advertencias = [], []

    for item in pedido.items:
        producto = (
            db.session.query(Producto)
            .filter_by(id=item.producto_id)
            .populate_existing()
            .with_for_update()
            .one()
        )
        stock_previo = producto.stock_actual

        producto.stock_actual = stock_previo - item.cantidad

        movimiento = MovimientoInventario(
            producto_id=producto.id,
            pedido_id=pedido.id,
            usuario_id=usuario_id,
            tipo=TipoMovimiento.SALIDA,
            cantidad=item.cantidad,
            stock_resultante=producto.stock_actual,
            motivo=f"Entrega confirmada del pedido {pedido.codigo}",
        )
        db.session.add(movimiento)
        movimientos.append(movimiento)

        # Un stock negativo revela un descuadre entre el inventario registrado y
        # el fisico. No se bloquea la entrega (la mercancia ya salio), pero se
        # deja constancia para que el administrador lo concilie.
        if producto.stock_actual < 0:
            advertencias.append(
                f"Descuadre de inventario en {producto.sku}: el stock registrado "
                f"era {stock_previo} y se despacharon {item.cantidad} unidades."
            )

    pedido.inventario_descontado = True
    return movimientos, advertencias


def _sincronizar_estado_ruta(ruta):
    """Mantiene el estado de la ruta alineado con el de sus paradas."""
    if ruta is None:
        return

    hay_movimiento = any(
        p.estado in (EstadoPedido.EN_RUTA,) or p.estado in EstadoPedido.FINALES
        for p in ruta.pedidos
    )
    # FINALES (no CERRADOS) para que una ruta con paradas canceladas tambien
    # pueda finalizarse: anular no es un intento de entrega, pero es tan
    # definitivo para la parada como uno entregado o fallido.
    todas_cerradas = bool(ruta.pedidos) and all(
        p.estado in EstadoPedido.FINALES for p in ruta.pedidos
    )

    if todas_cerradas:
        ruta.estado = EstadoRuta.FINALIZADA
        if ruta.finalizada_en is None:
            ruta.finalizada_en = ahora()
    elif hay_movimiento:
        ruta.estado = EstadoRuta.EN_CURSO
        ruta.finalizada_en = None
        if ruta.iniciada_en is None:
            ruta.iniciada_en = ahora()
    else:
        ruta.estado = EstadoRuta.PLANIFICADA


def cambiar_estado(
    pedido,
    nuevo_estado,
    usuario_id,
    nota=None,
    latitud=None,
    longitud=None,
    receptor_nombre=None,
    receptor_documento=None,
    motivo_fallo=None,
    observacion=None,
):
    """Aplica una transicion de estado sobre un pedido (RF4).

    Registra el evento en la bitacora, guarda la prueba de entrega cuando
    corresponde, descuenta el inventario si el pedido se marca como entregado
    (RF5) y actualiza el estado de la ruta.

    No hace commit: el controlador decide cuando confirmar la transaccion.
    """
    # Vuelve a leer el pedido con la fila bloqueada (`with_for_update`) antes de
    # decidir la transicion: el objeto que llega por parametro pudo cargarse
    # antes de que otra peticion concurrente (doble clic, reintento del
    # conductor por conectividad intermitente) ya lo hubiera cambiado de
    # estado. `populate_existing()` fuerza a refrescar sus columnas desde la
    # fila bloqueada aunque ya estuviera en el identity map de la sesion; sin
    # ella la consulta devolveria el mismo objeto con el estado obsoleto que
    # ya tenia en memoria Python.
    pedido = (
        db.session.query(Pedido)
        .filter_by(id=pedido.id)
        .populate_existing()
        .with_for_update()
        .one()
    )

    estado_anterior = pedido.estado

    if estado_anterior == nuevo_estado:
        raise TransicionInvalida(
            f"El pedido ya se encuentra en estado "
            f"{EstadoPedido.ETIQUETAS.get(nuevo_estado, nuevo_estado)}."
        )

    if not EstadoPedido.puede_transicionar(estado_anterior, nuevo_estado):
        raise TransicionInvalida(
            f"No es posible pasar de "
            f"{EstadoPedido.ETIQUETAS.get(estado_anterior, estado_anterior)} a "
            f"{EstadoPedido.ETIQUETAS.get(nuevo_estado, nuevo_estado)}."
        )

    pedido.estado = nuevo_estado

    movimientos, advertencias = [], []
    if nuevo_estado == EstadoPedido.ENTREGADO:
        movimientos, advertencias = _descontar_inventario(pedido, usuario_id)

    # Prueba de entrega: aplica tanto a la entrega exitosa como al intento fallido.
    if nuevo_estado in EstadoPedido.CERRADOS:
        prueba = pedido.prueba_entrega or PruebaEntrega(pedido_id=pedido.id)
        prueba.receptor_nombre = receptor_nombre or prueba.receptor_nombre
        prueba.receptor_documento = receptor_documento or prueba.receptor_documento
        prueba.observacion = observacion or prueba.observacion
        prueba.motivo_fallo = motivo_fallo if nuevo_estado == EstadoPedido.FALLIDO else None
        prueba.latitud = latitud if latitud is not None else prueba.latitud
        prueba.longitud = longitud if longitud is not None else prueba.longitud
        prueba.registrado_en = ahora()
        db.session.add(prueba)

    db.session.add(
        EventoPedido(
            pedido_id=pedido.id,
            usuario_id=usuario_id,
            estado_anterior=estado_anterior,
            estado_nuevo=nuevo_estado,
            nota=nota,
            latitud=latitud,
            longitud=longitud,
        )
    )

    _sincronizar_estado_ruta(pedido.ruta)

    return ResultadoTransicion(pedido, estado_anterior, movimientos, advertencias)


# Un pedido solo puede anularse antes de salir a reparto, o tras un intento
# fallido. EN_RUTA y ENTREGADO quedan fuera: el primero porque el conductor ya
# esta desplazado hacia el destino, el segundo porque revertir una entrega ya
# confirmada exigiria revertir tambien el descuento de inventario (RF5).
ESTADOS_ANULABLES = (EstadoPedido.PENDIENTE, EstadoPedido.ASIGNADO, EstadoPedido.FALLIDO)


def anular_pedido(pedido, usuario_id, motivo):
    """Cancela un pedido que aun no fue entregado (RF4).

    A diferencia de `cambiar_estado`, anular NO es un intento de entrega: no
    genera PruebaEntrega ni toca el inventario, solo deja constancia en la
    bitacora. El pedido conserva su ruta si tenia una asignada, de modo que el
    historico de la ruta y su avance reflejen la parada como resuelta (ver
    `_sincronizar_estado_ruta`, que trata CANCELADO como estado final).
    """
    # Igual que en cambiar_estado: bloquea la fila y la refresca (populate_existing)
    # para que una anulacion no se decida sobre un estado que otra peticion
    # concurrente ya cambio, ni sobre el que este objeto tenia cacheado.
    pedido = (
        db.session.query(Pedido)
        .filter_by(id=pedido.id)
        .populate_existing()
        .with_for_update()
        .one()
    )

    estado_anterior = pedido.estado

    if estado_anterior not in ESTADOS_ANULABLES:
        raise TransicionInvalida(
            f"No se puede anular un pedido en estado "
            f"{EstadoPedido.ETIQUETAS.get(estado_anterior, estado_anterior)}."
        )

    pedido.estado = EstadoPedido.CANCELADO

    db.session.add(
        EventoPedido(
            pedido_id=pedido.id,
            usuario_id=usuario_id,
            estado_anterior=estado_anterior,
            estado_nuevo=EstadoPedido.CANCELADO,
            nota=f"Pedido anulado: {motivo}",
        )
    )

    _sincronizar_estado_ruta(pedido.ruta)

    return pedido


def iniciar_ruta(ruta, usuario_id):
    """Marca en ruta todas las paradas asignadas de una ruta (RF4).

    Permite al conductor arrancar su jornada con una sola accion en lugar de
    actualizar parada por parada.
    """
    afectados, advertencias = [], []

    for pedido in ruta.pedidos:
        if pedido.estado == EstadoPedido.ASIGNADO:
            try:
                cambiar_estado(
                    pedido,
                    EstadoPedido.EN_RUTA,
                    usuario_id,
                    nota=f"Inicio de la ruta {ruta.codigo}",
                )
                afectados.append(pedido)
            except TransicionInvalida as error:
                advertencias.append(f"{pedido.codigo}: {error}")

    if ruta.iniciada_en is None and afectados:
        ruta.iniciada_en = ahora()
    _sincronizar_estado_ruta(ruta)

    return afectados, advertencias
