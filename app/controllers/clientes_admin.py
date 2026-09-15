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
from wtforms import BooleanField, StringField, TimeField
from wtforms.validators import DataRequired, Email, Length, Optional

from app.controllers.seguridad import requiere_rol
from app.extensions import db
from app.models import (
    Cliente, DireccionCliente, EstadoPedido, Pedido, Rol, normalizar_texto,
)

clientes_admin_bp = Blueprint("clientes_admin", __name__)


class FormularioCliente(FlaskForm):
    nombre = StringField(
        "Razon social",
        validators=[DataRequired("Indique el nombre del cliente."), Length(max=160)],
    )
    documento = StringField("NIT o documento", validators=[Optional(), Length(max=30)])
    telefono = StringField("Telefono", validators=[Optional(), Length(max=30)])
    correo = StringField(
        "Correo", validators=[Optional(), Email("Correo no valido."), Length(max=120)]
    )
    activo = BooleanField("Cliente activo", default=True)


class FormularioSede(FlaskForm):
    etiqueta = StringField("Etiqueta", validators=[Optional(), Length(max=80)])
    direccion = StringField(
        "Direccion", validators=[DataRequired("Indique la direccion."), Length(max=255)]
    )
    ciudad = StringField("Ciudad", validators=[Optional(), Length(max=80)], default="Bogota")
    ventana_inicio = TimeField("Ventana desde", validators=[Optional()])
    ventana_fin = TimeField("Ventana hasta", validators=[Optional()])


def _validar_unicidad(formulario, cliente=None):
    """El NIT es unico en el esquema; el nombre normalizado solo se advierte."""
    propio = cliente.id if cliente else None
    documento = (formulario.documento.data or "").strip()

    if documento:
        choque = db.session.query(Cliente).filter(Cliente.documento == documento)
        if propio:
            choque = choque.filter(Cliente.id != propio)
        if choque.first():
            formulario.documento.errors = list(formulario.documento.errors) + [
                "Ya existe un cliente con este NIT."
            ]
            return False

    # Un homonimo con NIT distinto es legitimo, pero casi siempre es un duplicado
    # por error de escritura: se bloquea el alta cuando no hay NIT que los separe.
    clave = normalizar_texto(formulario.nombre.data)
    choque = db.session.query(Cliente).filter(Cliente.nombre_normalizado == clave)
    if propio:
        choque = choque.filter(Cliente.id != propio)
    gemelo = choque.first()
    if gemelo is not None and not (documento and gemelo.documento):
        formulario.nombre.errors = list(formulario.nombre.errors) + [
            f"'{gemelo.nombre}' ya existe y no se distingue de este nombre. "
            "Indique el NIT de ambos si son empresas distintas."
        ]
        return False

    return True


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

    if formulario.validate_on_submit() and _validar_unicidad(formulario):
        cliente = Cliente(
            nombre=formulario.nombre.data.strip(),
            documento=(formulario.documento.data or "").strip() or None,
            telefono=(formulario.telefono.data or "").strip() or None,
            correo=(formulario.correo.data or "").strip() or None,
            activo=formulario.activo.data,
        )
        db.session.add(cliente)
        db.session.commit()
        flash(f"Cliente {cliente.nombre} registrado.", "exito")
        return redirect(url_for("clientes_admin.detalle", cliente_id=cliente.id))

    return render_template("admin/clientes/formulario.html", formulario=formulario, cliente=None)


@clientes_admin_bp.route("/<int:cliente_id>", methods=["GET", "POST"])
@login_required
@requiere_rol(Rol.ADMIN, Rol.DESPACHADOR)
def detalle(cliente_id):
    cliente = db.session.get(Cliente, cliente_id)
    if cliente is None:
        abort(404)

    formulario_sede = FormularioSede()
    if formulario_sede.validate_on_submit():
        direccion = formulario_sede.direccion.data.strip()
        ciudad = (formulario_sede.ciudad.data or "Bogota").strip()
        clave = normalizar_texto(direccion)

        duplicada = any(
            d.direccion_normalizada == clave
            and normalizar_texto(d.ciudad) == normalizar_texto(ciudad)
            for d in cliente.direcciones
        )
        if duplicada:
            flash("El cliente ya tiene registrada esa direccion.", "error")
        else:
            sede = DireccionCliente(
                cliente_id=cliente.id,
                etiqueta=(formulario_sede.etiqueta.data or "").strip()
                or f"Sede {len(cliente.direcciones) + 1}",
                direccion=direccion,
                ciudad=ciudad,
                ventana_inicio=formulario_sede.ventana_inicio.data,
                ventana_fin=formulario_sede.ventana_fin.data,
            )
            db.session.add(sede)
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
    )


@clientes_admin_bp.route("/<int:cliente_id>/editar", methods=["GET", "POST"])
@login_required
@requiere_rol(Rol.ADMIN, Rol.DESPACHADOR)
def editar(cliente_id):
    cliente = db.session.get(Cliente, cliente_id)
    if cliente is None:
        abort(404)

    formulario = FormularioCliente(obj=cliente)

    if formulario.validate_on_submit() and _validar_unicidad(formulario, cliente):
        # `renombrar` mantiene sincronizada la clave de deduplicacion; asignar
        # `nombre` a secas la dejaria apuntando al nombre viejo.
        cliente.renombrar(formulario.nombre.data.strip())
        cliente.documento = (formulario.documento.data or "").strip() or None
        cliente.telefono = (formulario.telefono.data or "").strip() or None
        cliente.correo = (formulario.correo.data or "").strip() or None
        cliente.activo = formulario.activo.data

        db.session.commit()
        flash(f"Cliente {cliente.nombre} actualizado.", "exito")
        return redirect(url_for("clientes_admin.detalle", cliente_id=cliente.id))

    return render_template(
        "admin/clientes/formulario.html", formulario=formulario, cliente=cliente
    )
