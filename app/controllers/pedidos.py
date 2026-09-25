"""RF2 - Ingreso de Ordenes de Despacho.

Permite al administrador y al gestor logistico registrar pedidos de forma manual
o mediante importacion masiva de archivos CSV exportados del ERP legado.
"""

import uuid
from datetime import datetime
from pathlib import Path

from flask import (
    Blueprint, Response, abort, current_app, flash, redirect,
    render_template, request, url_for,
)
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from sqlalchemy import or_
from sqlalchemy.orm import joinedload
from wtforms import (
    DateField, FloatField, SelectField, StringField, TextAreaField, TimeField,
)
from wtforms.validators import DataRequired, Length, NumberRange, Optional

from app.controllers.seguridad import requiere_rol
from app.extensions import db
from app.models import (
    Cliente, EstadoPedido, EventoPedido, Pedido, PedidoItem, Producto, Rol,
)
from app.services.clientes import resolver_destino, vincular_destino
from app.services.codigos import generar_codigo_pedido
from app.services.despacho import TransicionInvalida, anular_pedido
from app.services.importador import (
    ErrorImportacion, analizar_csv, generar_plantilla_csv, guardar_pedidos,
)
from app.tiempo import hoy

pedidos_bp = Blueprint("pedidos", __name__)

PRIORIDADES = [("1", "Alta"), ("2", "Media"), ("3", "Baja")]


class FormularioPedido(FlaskForm):
    """Carga manual de una orden (RF2)."""

    codigo = StringField("Codigo", validators=[Optional(), Length(max=40)])
    cliente_nombre = StringField(
        "Nombre del cliente", validators=[DataRequired("Indique el cliente."), Length(max=160)]
    )
    cliente_documento = StringField(
        "NIT o documento", validators=[Optional(), Length(max=30)]
    )
    cliente_telefono = StringField("Telefono", validators=[Optional(), Length(max=30)])
    direccion = StringField(
        "Direccion de entrega", validators=[DataRequired("Indique la direccion."), Length(max=255)]
    )
    ciudad = StringField("Ciudad", validators=[Optional(), Length(max=80)], default="Bogota")
    latitud = FloatField("Latitud", validators=[Optional(), NumberRange(-90, 90)])
    longitud = FloatField("Longitud", validators=[Optional(), NumberRange(-180, 180)])
    fecha_despacho = DateField("Fecha de despacho", validators=[DataRequired("Indique la fecha.")])
    ventana_inicio = TimeField("Ventana desde", validators=[Optional()])
    ventana_fin = TimeField("Ventana hasta", validators=[Optional()])
    prioridad = SelectField("Prioridad", choices=PRIORIDADES, default="3")
    observaciones = TextAreaField("Observaciones", validators=[Optional(), Length(max=1000)])

    def validar_ventana(self):
        if self.ventana_inicio.data and self.ventana_fin.data:
            if self.ventana_inicio.data >= self.ventana_fin.data:
                self.ventana_fin.errors = list(self.ventana_fin.errors) + [
                    "La hora final debe ser posterior a la inicial."
                ]
                return False
        return True


class FormularioImportacion(FlaskForm):
    """Solo aporta el token CSRF; el archivo se lee de request.files."""


def _leer_items_del_formulario():
    """Extrae las lineas de producto enviadas por el formulario dinamico.

    Devuelve (items, errores) donde items es una lista de (producto_id, cantidad).
    """
    ids = request.form.getlist("producto_id")
    cantidades = request.form.getlist("cantidad")
    items, errores = {}, []

    for indice, (producto_id, cantidad) in enumerate(zip(ids, cantidades), start=1):
        if not producto_id:
            continue
        try:
            producto_id = int(producto_id)
            cantidad = int(cantidad)
        except (TypeError, ValueError):
            errores.append(f"Linea {indice}: cantidad invalida.")
            continue
        if cantidad <= 0:
            errores.append(f"Linea {indice}: la cantidad debe ser mayor que cero.")
            continue
        # Un mismo producto repetido suma cantidades.
        items[producto_id] = items.get(producto_id, 0) + cantidad

    if not items and not errores:
        errores.append("Agregue al menos un producto al pedido.")

    return list(items.items()), errores


def _carpeta_temporal():
    carpeta = Path(current_app.instance_path) / "importaciones"
    carpeta.mkdir(parents=True, exist_ok=True)
    return carpeta


# --------------------------------------------------------------------------
# Listado y detalle
# --------------------------------------------------------------------------

@pedidos_bp.route("/")
@login_required
@requiere_rol(Rol.ADMIN, Rol.DESPACHADOR)
def lista():
    consulta = db.session.query(Pedido).options(joinedload(Pedido.ruta))

    estado = request.args.get("estado", "").strip()
    fecha_texto = request.args.get("fecha", "").strip()
    busqueda = request.args.get("q", "").strip()

    if estado and estado in EstadoPedido.ETIQUETAS:
        consulta = consulta.filter(Pedido.estado == estado)

    fecha_filtro = None
    if fecha_texto:
        try:
            fecha_filtro = datetime.strptime(fecha_texto, "%Y-%m-%d").date()
            consulta = consulta.filter(Pedido.fecha_despacho == fecha_filtro)
        except ValueError:
            flash("La fecha del filtro no es valida.", "advertencia")

    if busqueda:
        patron = f"%{busqueda}%"
        # El NIT se busca por la tabla de clientes; los demas campos siguen
        # consultando el snapshot del pedido, que es lo que se despacho.
        clientes_coincidentes = db.session.query(Cliente.id).filter(
            or_(Cliente.documento.ilike(patron), Cliente.nombre.ilike(patron))
        )
        consulta = consulta.filter(
            or_(
                Pedido.codigo.ilike(patron),
                Pedido.cliente_nombre.ilike(patron),
                Pedido.direccion.ilike(patron),
                Pedido.cliente_id.in_(clientes_coincidentes),
            )
        )

    pagina = request.args.get("pagina", 1, type=int)
    paginacion = consulta.order_by(
        Pedido.fecha_despacho.desc(), Pedido.prioridad, Pedido.codigo
    ).paginate(page=pagina, per_page=25, error_out=False)

    return render_template(
        "pedidos/lista.html",
        paginacion=paginacion,
        pedidos=paginacion.items,
        filtros={"estado": estado, "fecha": fecha_texto, "q": busqueda},
    )


@pedidos_bp.route("/<int:pedido_id>")
@login_required
@requiere_rol(Rol.ADMIN, Rol.DESPACHADOR)
def detalle(pedido_id):
    pedido = db.session.get(Pedido, pedido_id)
    if pedido is None:
        abort(404)
    return render_template("pedidos/detalle.html", pedido=pedido)


# --------------------------------------------------------------------------
# Alta manual
# --------------------------------------------------------------------------

@pedidos_bp.route("/nuevo", methods=["GET", "POST"])
@login_required
@requiere_rol(Rol.ADMIN, Rol.DESPACHADOR)
def nuevo():
    formulario = FormularioPedido()
    if not formulario.fecha_despacho.data:
        formulario.fecha_despacho.data = hoy()

    productos = (
        db.session.query(Producto)
        .filter(Producto.activo.is_(True))
        .order_by(Producto.nombre)
        .all()
    )

    items_enviados = []
    if formulario.validate_on_submit():
        items, errores_items = _leer_items_del_formulario()
        items_enviados = items
        ventana_ok = formulario.validar_ventana()

        codigo = (formulario.codigo.data or "").strip()
        if codigo and db.session.query(Pedido).filter_by(codigo=codigo).first():
            formulario.codigo.errors = list(formulario.codigo.errors) + [
                "Ya existe un pedido con este codigo."
            ]
            ventana_ok = False

        for error in errores_items:
            flash(error, "error")

        if not errores_items and ventana_ok:
            datos_destino = {
                "cliente_nombre": formulario.cliente_nombre.data.strip(),
                "cliente_documento": formulario.cliente_documento.data or None,
                "cliente_telefono": formulario.cliente_telefono.data or None,
                "direccion": formulario.direccion.data.strip(),
                "ciudad": formulario.ciudad.data or "Bogota",
                "latitud": formulario.latitud.data,
                "longitud": formulario.longitud.data,
                "ventana_inicio": formulario.ventana_inicio.data,
                "ventana_fin": formulario.ventana_fin.data,
            }
            # Reutiliza el cliente y la sede si ya existen; solo los crea cuando
            # el destino es realmente nuevo.
            cliente, direccion = resolver_destino(datos_destino)

            pedido = Pedido(
                codigo=codigo or generar_codigo_pedido(formulario.fecha_despacho.data),
                cliente_nombre=formulario.cliente_nombre.data.strip(),
                cliente_telefono=formulario.cliente_telefono.data or None,
                direccion=formulario.direccion.data.strip(),
                ciudad=formulario.ciudad.data or "Bogota",
                latitud=formulario.latitud.data,
                longitud=formulario.longitud.data,
                fecha_despacho=formulario.fecha_despacho.data,
                ventana_inicio=formulario.ventana_inicio.data,
                ventana_fin=formulario.ventana_fin.data,
                prioridad=int(formulario.prioridad.data),
                observaciones=formulario.observaciones.data or None,
                estado=EstadoPedido.PENDIENTE,
                creado_por_id=current_user.id,
            )
            vincular_destino(pedido, cliente, direccion)
            db.session.add(pedido)
            db.session.flush()

            for producto_id, cantidad in items:
                db.session.add(
                    PedidoItem(pedido_id=pedido.id, producto_id=producto_id, cantidad=cantidad)
                )

            db.session.add(
                EventoPedido(
                    pedido_id=pedido.id,
                    usuario_id=current_user.id,
                    estado_nuevo=EstadoPedido.PENDIENTE,
                    nota="Creado manualmente",
                )
            )
            db.session.commit()

            flash(f"Pedido {pedido.codigo} registrado correctamente.", "exito")
            return redirect(url_for("pedidos.detalle", pedido_id=pedido.id))

    return render_template(
        "pedidos/formulario.html",
        formulario=formulario,
        productos=productos,
        items_enviados=items_enviados,
    )


@pedidos_bp.route("/<int:pedido_id>/anular", methods=["POST"])
@login_required
@requiere_rol(Rol.ADMIN, Rol.DESPACHADOR)
def anular(pedido_id):
    pedido = db.session.get(Pedido, pedido_id)
    if pedido is None:
        abort(404)

    motivo = (request.form.get("motivo") or "").strip()
    if not motivo:
        flash("Indique el motivo de la anulacion.", "error")
        return redirect(url_for("pedidos.detalle", pedido_id=pedido.id))

    try:
        anular_pedido(pedido, current_user.id, motivo)
    except TransicionInvalida as error:
        flash(str(error), "error")
        return redirect(url_for("pedidos.detalle", pedido_id=pedido.id))

    db.session.commit()
    flash(f"Pedido {pedido.codigo} anulado.", "exito")
    return redirect(url_for("pedidos.lista"))


# --------------------------------------------------------------------------
# Importacion CSV
# --------------------------------------------------------------------------

@pedidos_bp.route("/importar", methods=["GET", "POST"])
@login_required
@requiere_rol(Rol.ADMIN, Rol.DESPACHADOR)
def importar():
    formulario = FormularioImportacion()
    contexto = {"formulario": formulario, "pedidos": None, "errores": None, "token": None}

    if formulario.validate_on_submit():
        archivo = request.files.get("archivo")
        if archivo is None or not archivo.filename:
            flash("Seleccione un archivo CSV.", "error")
            return render_template("pedidos/importar.html", **contexto)

        if not archivo.filename.lower().endswith(".csv"):
            flash("El archivo debe tener extension .csv", "error")
            return render_template("pedidos/importar.html", **contexto)

        contenido = archivo.read()
        if len(contenido) > 2 * 1024 * 1024:
            flash("El archivo supera el limite de 2 MB.", "error")
            return render_template("pedidos/importar.html", **contexto)

        try:
            pedidos, errores = analizar_csv(contenido)
        except ErrorImportacion as error:
            flash(str(error), "error")
            return render_template("pedidos/importar.html", **contexto)

        # Se guarda temporalmente para confirmar la carga sin volver a subirlo.
        token = uuid.uuid4().hex
        (_carpeta_temporal() / f"{token}.csv").write_bytes(contenido)

        contexto.update({"pedidos": pedidos, "errores": errores, "token": token})
        return render_template("pedidos/importar.html", **contexto)

    return render_template("pedidos/importar.html", **contexto)


@pedidos_bp.route("/importar/confirmar", methods=["POST"])
@login_required
@requiere_rol(Rol.ADMIN, Rol.DESPACHADOR)
def confirmar_importacion():
    token = request.form.get("token", "")
    if not token.isalnum() or len(token) != 32:
        flash("Solicitud de importacion invalida.", "error")
        return redirect(url_for("pedidos.importar"))

    ruta_archivo = _carpeta_temporal() / f"{token}.csv"
    if not ruta_archivo.exists():
        flash("La vista previa expiro. Vuelva a cargar el archivo.", "error")
        return redirect(url_for("pedidos.importar"))

    try:
        pedidos, errores = analizar_csv(ruta_archivo.read_bytes())
    except ErrorImportacion as error:
        flash(str(error), "error")
        return redirect(url_for("pedidos.importar"))
    finally:
        ruta_archivo.unlink(missing_ok=True)

    if not pedidos:
        flash("No hay pedidos validos para cargar.", "error")
        return redirect(url_for("pedidos.importar"))

    creados = guardar_pedidos(pedidos, current_user.id)

    mensaje = f"{creados} pedido(s) cargados correctamente."
    if errores:
        mensaje += f" Se omitieron {len(errores)} fila(s) con errores."
    flash(mensaje, "exito")
    return redirect(url_for("pedidos.lista"))


@pedidos_bp.route("/plantilla.csv")
@login_required
@requiere_rol(Rol.ADMIN, Rol.DESPACHADOR)
def plantilla():
    return Response(
        generar_plantilla_csv(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=plantilla_pedidos.csv"},
    )
