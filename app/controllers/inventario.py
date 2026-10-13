"""Gestion del almacen: catalogo de productos y movimientos de stock.

Soporta el objetivo especifico de sincronizar el inventario fisico del almacen
con los despachos, y alimenta el RF5.
"""

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from sqlalchemy import or_
from wtforms import BooleanField, IntegerField, SelectField, StringField, TextAreaField
from wtforms.validators import InputRequired, Length, NumberRange, Optional, ValidationError

from app.controllers.seguridad import requiere_rol
from app.extensions import db
from app.models import MovimientoInventario, Producto, Rol, TipoMovimiento
from app.services.inventario import (
    MOTIVO_AJUSTE_OBLIGATORIO, crear_producto, editar_producto, registrar_movimiento, sku_fijo,
)
from app.services.auditoria import auditar_pantalla, cambios_legibles, campos_cambiados
from app.services.validacion import DatosInvalidos

inventario_bp = Blueprint("inventario", __name__)


class FormularioProducto(FlaskForm):
    # Sin validadores propios: las reglas las aplica app.services.inventario, las
    # mismas que usa el asistente de voz. El stock inicial solo se pide en el
    # alta y entra como ENTRADA; despues el stock cambia solo con movimientos.
    sku = StringField("SKU", validators=[Optional()])
    nombre = StringField("Nombre", validators=[Optional()])
    descripcion = TextAreaField("Descripcion", validators=[Optional()])
    unidad = StringField("Unidad", validators=[Optional()], default="UND")
    stock_inicial = StringField("Stock inicial", validators=[Optional()], default="0")
    stock_minimo = StringField("Stock minimo", validators=[Optional()], default="0")
    activo = BooleanField("Producto activo", default=True)

    def datos(self):
        return {"sku": self.sku.data, "nombre": self.nombre.data, "descripcion": self.descripcion.data,
                "unidad": self.unidad.data, "stock_minimo": self.stock_minimo.data,
                "activo": self.activo.data}


def _mostrar_errores(formulario, error):
    """Ubica cada error del servicio junto a su campo, o como aviso general."""
    for campo, mensaje in error.errores:
        destino = getattr(formulario, campo, None) if campo else None
        if destino is not None:
            destino.errors = list(destino.errors) + [mensaje]
        else:
            flash(mensaje, "error")


class FormularioMovimiento(FlaskForm):
    tipo = SelectField(
        "Tipo de movimiento",
        choices=[
            (TipoMovimiento.ENTRADA, "Entrada (recepcion de mercancia)"),
            (TipoMovimiento.AJUSTE, "Ajuste por inventario fisico"),
        ],
    )
    # En un ajuste la cantidad es el stock contado: puede ser cero.
    cantidad = IntegerField(
        "Cantidad", validators=[InputRequired("Indique la cantidad."), NumberRange(min=0)]
    )
    motivo = StringField("Motivo", validators=[Length(max=255)])

    def validate_cantidad(self, campo):
        if self.tipo.data == TipoMovimiento.ENTRADA and (campo.data or 0) < 1:
            raise ValidationError("Una entrada debe ser de al menos una unidad.")

    def validate_motivo(self, campo):
        if self.tipo.data == TipoMovimiento.AJUSTE and not (campo.data or "").strip():
            raise ValidationError(MOTIVO_AJUSTE_OBLIGATORIO)


@inventario_bp.route("/")
@login_required
@requiere_rol(Rol.ADMIN, Rol.DESPACHADOR)
def lista():
    consulta = db.session.query(Producto)

    busqueda = request.args.get("q", "").strip()
    if busqueda:
        patron = f"%{busqueda}%"
        consulta = consulta.filter(
            or_(Producto.sku.ilike(patron), Producto.nombre.ilike(patron))
        )

    if request.args.get("criticos") == "1":
        consulta = consulta.filter(Producto.stock_actual <= Producto.stock_minimo)

    productos = consulta.order_by(Producto.nombre).all()
    return render_template(
        "inventario/lista.html",
        productos=productos,
        filtros={"q": busqueda, "criticos": request.args.get("criticos", "")},
    )


@inventario_bp.route("/nuevo", methods=["GET", "POST"])
@login_required
@requiere_rol(Rol.ADMIN, Rol.DESPACHADOR)
def nuevo():
    formulario = FormularioProducto()

    if formulario.validate_on_submit():
        try:
            producto = crear_producto({**formulario.datos(), "stock_inicial": formulario.stock_inicial.data},
                                      current_user.id)
        except DatosInvalidos as error:
            db.session.rollback()
            _mostrar_errores(formulario, error)
        else:
            auditar_pantalla("Crear producto", "producto", f"Creó el producto {producto.sku}, {producto.nombre}.",
                             {"sku": producto.sku, "nombre": producto.nombre, "unidad": producto.unidad,
                              "stock_minimo": producto.stock_minimo, "stock_inicial": producto.stock_actual,
                              "activo": producto.activo},
                             entidad_id=producto.id)
            db.session.commit()
            flash(f"Producto {producto.sku} creado.", "exito")
            return redirect(url_for("inventario.lista"))

    return render_template("inventario/formulario.html", formulario=formulario, producto=None,
                           sku_fijo=False)


@inventario_bp.route("/<int:producto_id>/editar", methods=["GET", "POST"])
@login_required
@requiere_rol(Rol.ADMIN, Rol.DESPACHADOR)
def editar(producto_id):
    producto = db.session.get(Producto, producto_id)
    if producto is None:
        abort(404)

    formulario = FormularioProducto(obj=producto)

    if formulario.validate_on_submit():
        try:
            cambios = editar_producto(producto, formulario.datos())
        except DatosInvalidos as error:
            db.session.rollback()
            _mostrar_errores(formulario, error)
        else:
            if cambios:
                auditar_pantalla("Editar producto", "producto",
                                 f"Editó el producto {producto.sku}: {campos_cambiados(cambios)}.",
                                 cambios_legibles(cambios), entidad_id=producto.id)
            db.session.commit()
            flash(f"Producto {producto.sku} actualizado.", "exito")
            return redirect(url_for("inventario.lista"))

    return render_template("inventario/formulario.html", formulario=formulario, producto=producto,
                           sku_fijo=sku_fijo(producto))


@inventario_bp.route("/<int:producto_id>", methods=["GET", "POST"])
@login_required
@requiere_rol(Rol.ADMIN, Rol.DESPACHADOR)
def detalle(producto_id):
    producto = db.session.get(Producto, producto_id)
    if producto is None:
        abort(404)

    formulario = FormularioMovimiento()
    if formulario.validate_on_submit():
        stock_previo = producto.stock_actual
        registrar_movimiento(
            producto,
            formulario.tipo.data,
            formulario.cantidad.data,
            current_user.id,
            motivo=formulario.motivo.data or None,
        )
        # Las entradas no se auditan: son operacion de alto volumen y ya quedan en
        # los movimientos. El ajuste, que fija el stock a un valor, si.
        if formulario.tipo.data == TipoMovimiento.AJUSTE:
            auditar_pantalla(
                "Ajustar inventario", "producto",
                f"Ajustó el inventario de {producto.sku}, {producto.nombre}: el stock pasó de "
                f"{stock_previo} a {formulario.cantidad.data}, por: {formulario.motivo.data.strip()}.",
                {"stock": [stock_previo, formulario.cantidad.data], "motivo": formulario.motivo.data.strip()},
                entidad_id=producto.id,
            )
        db.session.commit()
        flash("Movimiento registrado.", "exito")
        return redirect(url_for("inventario.detalle", producto_id=producto.id))

    movimientos = (
        producto.movimientos.order_by(MovimientoInventario.registrado_en.desc()).limit(40).all()
    )
    return render_template(
        "inventario/detalle.html",
        producto=producto,
        movimientos=movimientos,
        formulario=formulario,
    )
