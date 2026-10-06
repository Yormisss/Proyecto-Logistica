"""Alta de ordenes de despacho (RF2).

Reglas unicas para crear un pedido, compartidas por el formulario del gestor y
el asistente de voz: ninguno de los dos valida por su cuenta. La importacion
CSV conserva su propio flujo de vista previa (`app.services.importador`).
"""

from app.extensions import db
from app.models import EstadoPedido, EventoPedido, Pedido, PedidoItem, Producto
from app.services.clientes import vincular_destino
from app.services.codigos import generar_codigo_pedido

PRIORIDADES = {1: "Alta", 2: "Media", 3: "Baja"}
PRIORIDAD_POR_DEFECTO = 3


class PedidoInvalido(Exception):
    """El pedido no cumple las reglas. `errores` es una lista de (campo, mensaje).

    `campo` es el nombre del campo del formulario al que pertenece el error, o
    None para los errores generales (por ejemplo, los de los productos).
    """

    def __init__(self, errores):
        super().__init__("; ".join(mensaje for _, mensaje in errores))
        self.errores = errores


def validar_pedido(*, items, fecha_despacho, prioridad, ventana_inicio=None, ventana_fin=None,
                   codigo=None):
    """Valida los datos de un pedido nuevo.

    `items` es una lista de (producto_id, cantidad). Devuelve (items, errores):
    los items consolidados (un mismo producto repetido suma sus cantidades) y
    la lista de errores como (campo, mensaje); vacia si todo esta bien.
    """
    errores = []

    if fecha_despacho is None:
        errores.append(("fecha_despacho", "Indique la fecha de despacho."))

    if prioridad not in PRIORIDADES:
        errores.append(("prioridad", "La prioridad debe ser 1 (alta), 2 (media) o 3 (baja)."))

    if ventana_inicio and ventana_fin and ventana_inicio >= ventana_fin:
        errores.append(("ventana_fin", "La hora final debe ser posterior a la inicial."))

    codigo = (codigo or "").strip()
    if codigo and db.session.query(Pedido.id).filter_by(codigo=codigo).first():
        errores.append(("codigo", "Ya existe un pedido con este codigo."))

    consolidados = {}
    for producto_id, cantidad in items:
        if isinstance(cantidad, bool) or not isinstance(cantidad, int) or cantidad <= 0:
            errores.append((None, "La cantidad de cada producto debe ser un entero mayor que cero."))
            continue
        consolidados[producto_id] = consolidados.get(producto_id, 0) + cantidad

    if consolidados:
        activos = {
            producto_id
            for (producto_id,) in db.session.query(Producto.id).filter(
                Producto.id.in_(consolidados), Producto.activo.is_(True)
            )
        }
        if set(consolidados) - activos:
            errores.append((None, "Uno de los productos no existe o esta inactivo."))
    elif not errores:
        errores.append((None, "Agregue al menos un producto al pedido."))

    return list(consolidados.items()), errores


def destino_de_sede(cliente, sede):
    """Datos de entrega de un pedido para una sede ya registrada del cliente."""
    return {
        "cliente_nombre": cliente.nombre,
        "cliente_telefono": cliente.telefono,
        "direccion": sede.direccion,
        "ciudad": sede.ciudad,
        "latitud": sede.latitud,
        "longitud": sede.longitud,
        "ventana_inicio": sede.ventana_inicio,
        "ventana_fin": sede.ventana_fin,
    }


def crear_pedido(*, cliente, direccion, destino, items, fecha_despacho, prioridad, usuario_id,
                 codigo=None, observaciones=None, nota="Creado manualmente"):
    """Crea el pedido PENDIENTE con sus items y su primer evento. No hace commit.

    `cliente` y `direccion` son el Cliente y la DireccionCliente ya resueltos;
    `destino` es la copia historica de los datos de entrega (ver `Pedido`).
    Lanza PedidoInvalido si no cumple las reglas de `validar_pedido`.
    """
    codigo = (codigo or "").strip()
    items, errores = validar_pedido(
        items=items,
        fecha_despacho=fecha_despacho,
        prioridad=prioridad,
        ventana_inicio=destino.get("ventana_inicio"),
        ventana_fin=destino.get("ventana_fin"),
        codigo=codigo,
    )
    if errores:
        raise PedidoInvalido(errores)

    pedido = Pedido(
        codigo=codigo or generar_codigo_pedido(fecha_despacho),
        cliente_nombre=destino["cliente_nombre"],
        cliente_telefono=destino.get("cliente_telefono"),
        direccion=destino["direccion"],
        ciudad=destino.get("ciudad") or "Bogota",
        latitud=destino.get("latitud"),
        longitud=destino.get("longitud"),
        fecha_despacho=fecha_despacho,
        ventana_inicio=destino.get("ventana_inicio"),
        ventana_fin=destino.get("ventana_fin"),
        prioridad=prioridad,
        observaciones=observaciones or None,
        estado=EstadoPedido.PENDIENTE,
        creado_por_id=usuario_id,
    )
    vincular_destino(pedido, cliente, direccion)
    db.session.add(pedido)
    db.session.flush()

    for producto_id, cantidad in items:
        db.session.add(PedidoItem(pedido_id=pedido.id, producto_id=producto_id, cantidad=cantidad))

    db.session.add(
        EventoPedido(
            pedido_id=pedido.id,
            usuario_id=usuario_id,
            estado_nuevo=EstadoPedido.PENDIENTE,
            nota=nota,
        )
    )
    return pedido
