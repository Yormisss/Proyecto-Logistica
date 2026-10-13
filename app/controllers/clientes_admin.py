"""Directorio de clientes comerciales (vista interna).

Complemento de `app/controllers/cliente.py`, que es el portal del destinatario:
aqui el personal del centro de distribucion administra la entidad de negocio
—razon social, NIT, sedes— y ve de un golpe quien tiene acceso al portal y quien
no. Antes esa pregunta solo se respondia consultando la base.

Los clientes tampoco se eliminan: `pedidos.cliente_id` los referencia y borrarlos
dejaria ordenes historicas sin destinatario. Se desactivan.
"""

from flask import (
    Blueprint, abort, flash, redirect, render_template, request, url_for,
)
from flask_login import login_required
from flask_wtf import FlaskForm
from sqlalchemy import func, or_
from wtforms import BooleanField, StringField
from wtforms.validators import Optional

from app.controllers.seguridad import requiere_rol
from app.extensions import db
from app.models import Cliente, DireccionCliente, EstadoPedido, Pedido, Rol
from app.services.auditoria import auditar_pantalla, cambios_legibles, campos_cambiados
from app.services.clientes import (
    cambiar_estado_sede, crear_cliente, crear_sede, editar_cliente, editar_sede,
)
from app.services.validacion import DatosInvalidos

clientes_admin_bp = Blueprint("clientes_admin", __name__)


class FormularioCliente(FlaskForm):
    # Sin validadores propios: las reglas las aplica app.services.clientes, las
    # mismas que usa el asistente de voz.
    nombre = StringField("Nombre o razón social", validators=[Optional()])
    documento = StringField("NIT o documento", validators=[Optional()])
    telefono = StringField("Teléfono", validators=[Optional()])
    correo = StringField("Correo", validators=[Optional()])
    activo = BooleanField("Cliente activo", default=True)

    def datos(self):
        return {"nombre": self.nombre.data, "documento": self.documento.data,
                "telefono": self.telefono.data, "correo": self.correo.data, "activo": self.activo.data}


class FormularioSede(FlaskForm):
    etiqueta = StringField("Etiqueta", validators=[Optional()])
    direccion = StringField("Dirección", validators=[Optional()])
    ciudad = StringField("Ciudad", validators=[Optional()], default="Bogota")
    latitud = StringField("Latitud", validators=[Optional()])
    longitud = StringField("Longitud", validators=[Optional()])
    ventana_inicio = StringField("Ventana desde", validators=[Optional()])
    ventana_fin = StringField("Ventana hasta", validators=[Optional()])

    def datos(self):
        return {campo: getattr(self, campo).data for campo in
                ("etiqueta", "direccion", "ciudad", "latitud", "longitud", "ventana_inicio", "ventana_fin")}


class FormularioAccion(FlaskForm):
    """Solo aporta el token CSRF."""


def _mostrar_errores(formulario, error, todo_como_aviso=False):
    """Ubica cada error del servicio junto a su campo, o como aviso general."""
    for campo, mensaje in error.errores:
        destino = getattr(formulario, campo, None) if campo and not todo_como_aviso else None
        if destino is not None:
            destino.errors = list(destino.errors) + [mensaje]
        else:
            flash(mensaje, "error")


def _cliente(cliente_id):
    cliente = db.session.get(Cliente, cliente_id)
    if cliente is None:
        abort(404)
    return cliente


def _sede(cliente, sede_id):
    sede = db.session.get(DireccionCliente, sede_id)
    if sede is None or sede.cliente_id != cliente.id:
        abort(404)
    return sede


@clientes_admin_bp.route("/")
@login_required
@requiere_rol(Rol.ADMIN, Rol.DESPACHADOR)
def lista():
    # Conteos agregados en una sola consulta por metrica, en vez de una consulta
    # por cliente dentro de la plantilla.
    pedidos_por_cliente = dict(
        db.session.query(Pedido.cliente_id, func.count(Pedido.id))
        .group_by(Pedido.cliente_id)
        .all()
    )
    abiertos_por_cliente = dict(
        db.session.query(Pedido.cliente_id, func.count(Pedido.id))
        .filter(Pedido.estado.in_(EstadoPedido.ABIERTOS))
        .group_by(Pedido.cliente_id)
        .all()
    )
    sedes_por_cliente = dict(
        db.session.query(DireccionCliente.cliente_id, func.count(DireccionCliente.id))
        .group_by(DireccionCliente.cliente_id)
        .all()
    )

    consulta = db.session.query(Cliente)

    busqueda = request.args.get("q", "").strip()
    if busqueda:
        patron = f"%{busqueda}%"
        consulta = consulta.filter(
            or_(Cliente.nombre.ilike(patron), Cliente.documento.ilike(patron))
        )

    portal = request.args.get("portal", "").strip()
    if portal == "si":
        consulta = consulta.filter(Cliente.usuario_id.isnot(None))
    elif portal == "no":
        consulta = consulta.filter(Cliente.usuario_id.is_(None))
    else:
        portal = ""

    clientes = consulta.order_by(Cliente.nombre).all()

    return render_template(
        "admin/clientes/lista.html",
        clientes=clientes,
        pedidos_por_cliente=pedidos_por_cliente,
        abiertos_por_cliente=abiertos_por_cliente,
        sedes_por_cliente=sedes_por_cliente,
        con_portal=sum(1 for c in clientes if c.usuario_id),
        filtros={"q": busqueda, "portal": portal},
    )


@clientes_admin_bp.route("/nuevo", methods=["GET", "POST"])
@login_required
@requiere_rol(Rol.ADMIN, Rol.DESPACHADOR)
def nuevo():
    """Alta manual de un cliente que aun no ha hecho pedidos.

    La via habitual es la importacion CSV, que los crea sola; esto cubre el caso
    de habilitar el portal de un cliente antes de su primer despacho.
    """
    formulario = FormularioCliente()

    if formulario.validate_on_submit():
        try:
            cliente = crear_cliente(formulario.datos())
        except DatosInvalidos as error:
            _mostrar_errores(formulario, error)
        else:
            auditar_pantalla("Crear cliente", "cliente", f"Registró el cliente {cliente.nombre}.",
                             {campo: getattr(cliente, campo) for campo in
                              ("nombre", "documento", "telefono", "correo", "activo")},
                             entidad_id=cliente.id)
            db.session.commit()
            flash(f"Cliente {cliente.nombre} registrado.", "exito")
            return redirect(url_for("clientes_admin.detalle", cliente_id=cliente.id))

    return render_template("admin/clientes/formulario.html", formulario=formulario, cliente=None)


@clientes_admin_bp.route("/<int:cliente_id>", methods=["GET", "POST"])
@login_required
@requiere_rol(Rol.ADMIN, Rol.DESPACHADOR)
def detalle(cliente_id):
    cliente = _cliente(cliente_id)

    formulario_sede = FormularioSede()
    if formulario_sede.validate_on_submit():
        try:
            sede = crear_sede(cliente, formulario_sede.datos())
        except DatosInvalidos as error:
            # El formulario de la sede vive dentro de un desplegable: los errores
            # se muestran como avisos para que no queden ocultos.
            _mostrar_errores(formulario_sede, error, todo_como_aviso=True)
        else:
            auditar_pantalla("Agregar sede", "sede",
                             f"Agregó la sede {sede.etiqueta} de {cliente.nombre}: {sede.direccion}.",
                             {"cliente": cliente.nombre, **cambios_legibles(
                                 {campo: (None, getattr(sede, campo)) for campo in formulario_sede.datos()})},
                             entidad_id=sede.id)
            db.session.commit()
            flash(f"Sede '{sede.etiqueta}' agregada.", "exito")
            return redirect(url_for("clientes_admin.detalle", cliente_id=cliente.id))

    pedidos = (
        db.session.query(Pedido)
        .filter(Pedido.cliente_id == cliente.id)
        .order_by(Pedido.fecha_despacho.desc(), Pedido.codigo.desc())
        .limit(15)
        .all()
    )

    return render_template(
        "admin/clientes/detalle.html",
        cliente=cliente,
        pedidos=pedidos,
        total_pedidos=cliente.pedidos.count(),
        formulario_sede=formulario_sede,
        formulario_accion=FormularioAccion(),
    )


@clientes_admin_bp.route("/<int:cliente_id>/editar", methods=["GET", "POST"])
@login_required
@requiere_rol(Rol.ADMIN, Rol.DESPACHADOR)
def editar(cliente_id):
    cliente = _cliente(cliente_id)
    formulario = FormularioCliente(obj=cliente)

    if formulario.validate_on_submit():
        try:
            cambios = editar_cliente(cliente, formulario.datos())
        except DatosInvalidos as error:
            db.session.rollback()
            _mostrar_errores(formulario, error)
        else:
            if cambios:
                auditar_pantalla("Editar cliente", "cliente",
                                 f"Editó el cliente {cliente.nombre}: {campos_cambiados(cambios)}.",
                                 cambios_legibles(cambios), entidad_id=cliente.id)
            db.session.commit()
            flash(f"Cliente {cliente.nombre} actualizado.", "exito")
            return redirect(url_for("clientes_admin.detalle", cliente_id=cliente.id))

    return render_template(
        "admin/clientes/formulario.html", formulario=formulario, cliente=cliente
    )


@clientes_admin_bp.route("/<int:cliente_id>/sedes/<int:sede_id>/editar", methods=["GET", "POST"])
@login_required
@requiere_rol(Rol.ADMIN, Rol.DESPACHADOR)
def editar_sede_vista(cliente_id, sede_id):
    cliente = _cliente(cliente_id)
    sede = _sede(cliente, sede_id)
    formulario = FormularioSede(obj=sede)
    if formulario.validate_on_submit():
        try:
            cambios = editar_sede(sede, formulario.datos())
        except DatosInvalidos as error:
            db.session.rollback()
            _mostrar_errores(formulario, error)
        else:
            if cambios:
                auditar_pantalla("Editar sede", "sede",
                                 f"Editó la sede {sede.etiqueta} de {cliente.nombre}: "
                                 f"{campos_cambiados(cambios)}.",
                                 cambios_legibles(cambios), entidad_id=sede.id)
            db.session.commit()
            flash(f"Sede '{sede.etiqueta}' actualizada. Los pedidos ya creados conservan su "
                  "direccion.", "exito")
            return redirect(url_for("clientes_admin.detalle", cliente_id=cliente.id))
    elif not formulario.is_submitted():
        formulario.ventana_inicio.data = sede.ventana_inicio.strftime("%H:%M") if sede.ventana_inicio else ""
        formulario.ventana_fin.data = sede.ventana_fin.strftime("%H:%M") if sede.ventana_fin else ""
    return render_template("admin/clientes/sede.html", formulario=formulario, cliente=cliente, sede=sede)


@clientes_admin_bp.route("/<int:cliente_id>/sedes/<int:sede_id>/estado", methods=["POST"])
@login_required
@requiere_rol(Rol.ADMIN, Rol.DESPACHADOR)
def alternar_sede(cliente_id, sede_id):
    cliente = _cliente(cliente_id)
    sede = _sede(cliente, sede_id)
    if not FormularioAccion().validate_on_submit():
        flash("Solicitud invalida. Intente nuevamente.", "error")
        return redirect(url_for("clientes_admin.detalle", cliente_id=cliente.id))
    try:
        cambiar_estado_sede(sede, not sede.activa)
    except DatosInvalidos as error:
        flash(str(error), "error")
        return redirect(url_for("clientes_admin.detalle", cliente_id=cliente.id))
    auditar_pantalla("Activar sede" if sede.activa else "Desactivar sede", "sede",
                     f"{'Activó' if sede.activa else 'Desactivó'} la sede {sede.etiqueta} de {cliente.nombre}.",
                     {"activa": [not sede.activa, sede.activa]}, entidad_id=sede.id)
    db.session.commit()
    flash(f"Sede '{sede.etiqueta}' " + ("activada." if sede.activa else
          "desactivada: no se ofrece para pedidos nuevos."), "exito")
    return redirect(url_for("clientes_admin.detalle", cliente_id=cliente.id))
