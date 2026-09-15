"""Portal de seguimiento del cliente destinatario (actor 2.2.4).

El cliente consulta unicamente SUS ordenes. Deliberadamente no se expone la
entidad `Ruta`: una ruta agrupa las paradas de varios clientes e incluye sus
direcciones, telefonos, el conductor y la geometria completa del recorrido, de
modo que mostrarla filtraria datos de terceros. Lo que el cliente necesita
—donde va su pedido— se responde con el estado de la orden y su bitacora, que
ya se registra para el RF4.
"""

from flask import Blueprint, abort, render_template, request
from flask_login import current_user, login_required
from sqlalchemy import func

from app.controllers.seguridad import requiere_rol
from app.extensions import db
from app.models import DireccionCliente, EstadoPedido, Pedido, Rol

cliente_bp = Blueprint("cliente", __name__)

# Texto que ve el cliente para cada estado interno. La bitacora se traduce en
# vez de mostrarse cruda: notas como "Creado por importacion CSV" son de uso
# interno y no aportan nada al destinatario.
SEGUIMIENTO_PUBLICO = {
    EstadoPedido.PENDIENTE: "Pedido recibido",
    EstadoPedido.ASIGNADO: "Programado para despacho",
    EstadoPedido.EN_RUTA: "En camino a su direccion",
    EstadoPedido.ENTREGADO: "Entregado",
    EstadoPedido.FALLIDO: "Entrega no lograda",
}

FILTROS = {
    "abiertos": EstadoPedido.ABIERTOS,
    "cerrados": EstadoPedido.CERRADOS,
}


def _cliente_actual():
    """Cliente comercial vinculado a la sesion.

    Una cuenta con rol CLIENTE sin `Cliente` asociado no puede ver nada: es un
    registro a medio configurar, no un permiso para consultar toda la operacion.
    """
    cliente = current_user.cliente
    if cliente is None or not cliente.activo:
        abort(403)
    return cliente


def _pedido_del_cliente(pedido_id):
    """Garantiza que el cliente solo consulte sus propias ordenes (RNF5).

    Mismo criterio que `_pedido_del_conductor`: el filtro va contra la clave
    ajena, no contra el nombre, para que cambiar el id de la URL no revele
    pedidos ajenos.
    """
    cliente = _cliente_actual()
    pedido = db.session.get(Pedido, pedido_id)
    if pedido is None:
        abort(404)
    if pedido.cliente_id != cliente.id:
        abort(403)
    return pedido


def _resumen(cliente):
    """Conteo por estado de todas las ordenes del cliente."""
    filas = (
        db.session.query(Pedido.estado, func.count(Pedido.id))
        .filter(Pedido.cliente_id == cliente.id)
        .group_by(Pedido.estado)
        .all()
    )
    conteo = dict(filas)
    return {
        "total": sum(conteo.values()),
        "en_curso": sum(conteo.get(e, 0) for e in EstadoPedido.ABIERTOS),
        "entregados": conteo.get(EstadoPedido.ENTREGADO, 0),
        "fallidos": conteo.get(EstadoPedido.FALLIDO, 0),
    }


@cliente_bp.route("/")
@login_required
@requiere_rol(Rol.CLIENTE)
def mis_pedidos():
    cliente = _cliente_actual()

    consulta = db.session.query(Pedido).filter(Pedido.cliente_id == cliente.id)

    filtro = request.args.get("estado", "").strip()
    if filtro in FILTROS:
        consulta = consulta.filter(Pedido.estado.in_(FILTROS[filtro]))
    elif filtro in EstadoPedido.ETIQUETAS:
        consulta = consulta.filter(Pedido.estado == filtro)
    else:
        filtro = ""

    pagina = request.args.get("pagina", 1, type=int)
    paginacion = consulta.order_by(
        Pedido.fecha_despacho.desc(), Pedido.codigo.desc()
    ).paginate(page=pagina, per_page=20, error_out=False)

    return render_template(
        "cliente/pedidos.html",
        cliente=cliente,
        paginacion=paginacion,
        pedidos=paginacion.items,
        resumen=_resumen(cliente),
        filtro=filtro,
        seguimiento=SEGUIMIENTO_PUBLICO,
    )


@cliente_bp.route("/pedido/<int:pedido_id>")
@login_required
@requiere_rol(Rol.CLIENTE)
def seguimiento(pedido_id):
    pedido = _pedido_del_cliente(pedido_id)

    # Bitacora depurada: solo el hito y el momento. Se omiten el usuario que lo
    # registro y las notas internas.
    hitos = [
        {
            "texto": SEGUIMIENTO_PUBLICO.get(evento.estado_nuevo, evento.estado_nuevo),
            "estado": evento.estado_nuevo,
            "momento": evento.registrado_en,
        }
        for evento in pedido.eventos
    ]

    return render_template(
        "cliente/seguimiento.html",
        pedido=pedido,
        hitos=hitos,
        seguimiento=SEGUIMIENTO_PUBLICO,
    )


@cliente_bp.route("/direcciones")
@login_required
@requiere_rol(Rol.CLIENTE)
def direcciones():
    """Sedes registradas del cliente, con su ventana horaria habitual."""
    cliente = _cliente_actual()
    sedes = (
        db.session.query(DireccionCliente)
        .filter_by(cliente_id=cliente.id, activa=True)
        .order_by(DireccionCliente.etiqueta)
        .all()
    )
    return render_template("cliente/direcciones.html", cliente=cliente, sedes=sedes)
