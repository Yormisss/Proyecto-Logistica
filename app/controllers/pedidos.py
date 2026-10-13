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
    Cliente, EstadoPedido, Pedido, Producto, Rol,
)
from app.services.clientes import resolver_destino
from app.services.pedidos import (
    AVISO_RECALCULAR, campos_editables, crear_pedido, editar_pedido, validar_pedido,
)
from app.services.validacion import DatosInvalidos
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


class FormularioEdicion(FlaskForm):
    """Edicion de un pedido; los campos que aplican dependen de su estado."""

    fecha_despacho = StringField("Fecha de despacho", validators=[Optional()])
    sede_id = SelectField("Sede", coerce=int, validate_choice=False, validators=[Optional()])
    ventana_inicio = StringField("Ventana desde", validators=[Optional()])
    ventana_fin = StringField("Ventana hasta", validators=[Optional()])
    prioridad = SelectField("Prioridad", choices=PRIORIDADES, validators=[Optional()])
    observaciones = TextAreaField("Observaciones", validators=[Optional()])


class FormularioImportacion(FlaskForm):
    """Solo aporta el token CSRF; el archivo se lee de request.files."""


def _leer_items_del_formulario():
    """Extrae las lineas de producto enviadas por el formulario dinamico.

    Solo convierte el texto a numeros; las reglas (cantidad positiva, producto
    activo, al menos uno) las aplica `app.services.pedidos`. Devuelve
    (items, errores) donde items es una lista de (producto_id, cantidad).
    """
    ids = request.form.getlist("producto_id")
    cantidades = request.form.getlist("cantidad")
    items, errores = [], []

    for indice, (producto_id, cantidad) in enumerate(zip(ids, cantidades), start=1):
        if not producto_id:
            continue
        try:
            items.append((int(producto_id), int(cantidad)))
        except (TypeError, ValueError):
            errores.append(f"Linea {indice}: cantidad invalida.")

    return items, errores


def _mostrar_errores(formulario, errores):
    """Ubica cada error del servicio junto a su campo, o como aviso general."""
    for campo, mensaje in errores:
        destino = getattr(formulario, campo, None) if campo else None
        if destino is not None:
            destino.errors = list(destino.errors) + [mensaje]
        else:
            flash(mensaje, "error")


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
    return render_template("pedidos/detalle.html", pedido=pedido,
                           editable=bool(campos_editables(pedido)))


@pedidos_bp.route("/<int:pedido_id>/editar", methods=["GET", "POST"])
@login_required
@requiere_rol(Rol.ADMIN, Rol.DESPACHADOR)
def editar(pedido_id):
    """Edicion de un pedido: PENDIENTE casi todo; ASIGNADO solo ventana, prioridad
    y observaciones; los demas estados no se editan. Reglas en app.services.pedidos."""
    pedido = db.session.get(Pedido, pedido_id)
    if pedido is None:
        abort(404)
    editables = campos_editables(pedido)
    if not editables:
        flash(f"Un pedido {pedido.estado_etiqueta.lower()} no se puede editar.", "error")
        return redirect(url_for("pedidos.detalle", pedido_id=pedido.id))

    formulario = FormularioEdicion()
    sedes = [s for s in pedido.cliente.direcciones if s.activa] if pedido.cliente else []
    formulario.sede_id.choices = [(s.id, f"{s.etiqueta} — {s.direccion}") for s in sedes]
    productos = (
        db.session.query(Producto).filter(Producto.activo.is_(True)).order_by(Producto.nombre).all()
    )
    items_enviados = [(i.producto_id, i.cantidad) for i in pedido.items]

    if formulario.validate_on_submit():
        datos = {campo: getattr(formulario, campo).data for campo in
                 ("fecha_despacho", "sede_id", "ventana_inicio", "ventana_fin", "prioridad", "observaciones")
                 if campo in editables}
        errores_lectura = []
        if "items" in editables:
            datos["items"], errores_lectura = _leer_items_del_formulario()
            items_enviados = datos["items"]
        for error in errores_lectura:
            flash(error, "error")
        if not errores_lectura:
            try:
                _, advertencias = editar_pedido(pedido, datos, current_user.id)
            except DatosInvalidos as error:
                db.session.rollback()
                _mostrar_errores(formulario, error.errores)
            else:
                db.session.commit()
                flash(f"Pedido {pedido.codigo} actualizado.", "exito")
                for advertencia in advertencias:
                    flash(advertencia, "advertencia")
                return redirect(url_for("pedidos.detalle", pedido_id=pedido.id))
    elif not formulario.is_submitted():
        formulario.fecha_despacho.data = pedido.fecha_despacho.isoformat()
        formulario.sede_id.data = pedido.direccion_id
        formulario.ventana_inicio.data = pedido.ventana_inicio.strftime("%H:%M") if pedido.ventana_inicio else ""
        formulario.ventana_fin.data = pedido.ventana_fin.strftime("%H:%M") if pedido.ventana_fin else ""
        formulario.prioridad.data = str(pedido.prioridad)
        formulario.observaciones.data = pedido.observaciones or ""

    return render_template("pedidos/editar.html", pedido=pedido, formulario=formulario,
                           editables=editables, productos=productos, items_enviados=items_enviados,
                           aviso_recalcular=AVISO_RECALCULAR)


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
        items, errores_lectura = _leer_items_del_formulario()
        items_enviados = items
        for error in errores_lectura:
            flash(error, "error")

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
        reglas = {
            "items": items,
            "fecha_despacho": formulario.fecha_despacho.data,
            "prioridad": int(formulario.prioridad.data),
            "codigo": formulario.codigo.data,
        }
        # Se valida antes de resolver el destino: resolver_destino puede crear
        # el cliente o la sede, y no deben quedar si el pedido se rechaza.
        _, errores = validar_pedido(
            ventana_inicio=datos_destino["ventana_inicio"],
            ventana_fin=datos_destino["ventana_fin"],
            **reglas,
        )
        _mostrar_errores(formulario, errores)

        if not errores and not errores_lectura:
            # Reutiliza el cliente y la sede si ya existen; solo los crea cuando
            # el destino es realmente nuevo.
            cliente, direccion = resolver_destino(datos_destino)
            pedido = crear_pedido(
                cliente=cliente,
                direccion=direccion,
                destino=datos_destino,
                usuario_id=current_user.id,
                observaciones=formulario.observaciones.data,
                **reglas,
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
