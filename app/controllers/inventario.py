"""Gestion del almacen: catalogo de productos y movimientos de stock.

Soporta el objetivo especifico de sincronizar el inventario fisico del almacen
con los despachos, y alimenta el RF5.
"""

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from sqlalchemy import or_
from wtforms import BooleanField, IntegerField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, NumberRange, Optional

from app.controllers.seguridad import requiere_rol
from app.extensions import db
from app.models import MovimientoInventario, Producto, Rol, TipoMovimiento

inventario_bp = Blueprint("inventario", __name__)


class FormularioProducto(FlaskForm):
    sku = StringField("SKU", validators=[DataRequired("Indique el SKU."), Length(max=40)])
    nombre = StringField("Nombre", validators=[DataRequired("Indique el nombre."), Length(max=160)])
    descripcion = TextAreaField("Descripcion", validators=[Optional(), Length(max=255)])
    unidad = StringField("Unidad", validators=[Optional(), Length(max=20)], default="UND")
    stock_actual = IntegerField("Stock actual", validators=[DataRequired(), NumberRange(min=0)], default=0)
    stock_minimo = IntegerField("Stock minimo", validators=[DataRequired(), NumberRange(min=0)], default=0)
    activo = BooleanField("Producto activo", default=True)


class FormularioMovimiento(FlaskForm):
    tipo = SelectField(
        "Tipo de movimiento",
        choices=[
            (TipoMovimiento.ENTRADA, "Entrada (recepcion de mercancia)"),
            (TipoMovimiento.AJUSTE, "Ajuste por inventario fisico"),
        ],
    )
    cantidad = IntegerField(
        "Cantidad", validators=[DataRequired("Indique la cantidad."), NumberRange(min=1)]
    )
    motivo = StringField("Motivo", validators=[Optional(), Length(max=255)])


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
    return movimiento


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
        sku = formulario.sku.data.strip().upper()
        if db.session.query(Producto).filter_by(sku=sku).first():
            formulario.sku.errors = list(formulario.sku.errors) + ["Ya existe un producto con este SKU."]
        else:
            # El producto nace en cero y el stock inicial entra como movimiento,
            # de modo que todo el stock quede respaldado por su trazabilidad.
            producto = Producto(
                sku=sku,
                nombre=formulario.nombre.data.strip(),
                descripcion=formulario.descripcion.data or None,
                unidad=formulario.unidad.data or "UND",
                stock_actual=0,
                stock_minimo=formulario.stock_minimo.data,
                activo=formulario.activo.data,
            )
            db.session.add(producto)
            db.session.flush()

            if formulario.stock_actual.data:
                registrar_movimiento(
                    producto, TipoMovimiento.ENTRADA, formulario.stock_actual.data,
                    current_user.id, motivo="Stock inicial",
                )

            db.session.commit()
            flash(f"Producto {producto.sku} creado.", "exito")
            return redirect(url_for("inventario.lista"))

    return render_template("inventario/formulario.html", formulario=formulario, producto=None)


@inventario_bp.route("/<int:producto_id>/editar", methods=["GET", "POST"])
@login_required
@requiere_rol(Rol.ADMIN, Rol.DESPACHADOR)
def editar(producto_id):
    producto = db.session.get(Producto, producto_id)
    if producto is None:
        abort(404)

    formulario = FormularioProducto(obj=producto)

    if formulario.validate_on_submit():
        sku = formulario.sku.data.strip().upper()
        existente = db.session.query(Producto).filter(
            Producto.sku == sku, Producto.id != producto.id
        ).first()

        if existente:
            formulario.sku.errors = list(formulario.sku.errors) + ["Ya existe otro producto con este SKU."]
        else:
            stock_anterior = producto.stock_actual

            producto.sku = sku
            producto.nombre = formulario.nombre.data.strip()
            producto.descripcion = formulario.descripcion.data or None
            producto.unidad = formulario.unidad.data or "UND"
            producto.stock_minimo = formulario.stock_minimo.data
            producto.activo = formulario.activo.data

            if formulario.stock_actual.data != stock_anterior:
                registrar_movimiento(
                    producto, TipoMovimiento.AJUSTE, formulario.stock_actual.data,
                    current_user.id, motivo="Ajuste desde edicion del producto",
                )

            db.session.commit()
            flash(f"Producto {producto.sku} actualizado.", "exito")
            return redirect(url_for("inventario.lista"))

    return render_template("inventario/formulario.html", formulario=formulario, producto=producto)


@inventario_bp.route("/<int:producto_id>", methods=["GET", "POST"])
@login_required
@requiere_rol(Rol.ADMIN, Rol.DESPACHADOR)
def detalle(producto_id):
    producto = db.session.get(Producto, producto_id)
    if producto is None:
        abort(404)

    formulario = FormularioMovimiento()
    if formulario.validate_on_submit():
        registrar_movimiento(
            producto,
            formulario.tipo.data,
            formulario.cantidad.data,
            current_user.id,
            motivo=formulario.motivo.data or None,
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
