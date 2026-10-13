"""RF3 - Planificacion y generacion de rutas de distribucion.

Sustituye la asignacion empirica, estatica y manual de rutas identificada en el
diagrama de Ishikawa (categoria Metodos) por una secuencia calculada a partir de
un servicio de geolocalizacion.
"""

import json
from datetime import datetime

from flask import (
    Blueprint, abort, flash, redirect, render_template, request, url_for,
)
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from sqlalchemy.orm import joinedload
from wtforms import DateField, SelectField
from wtforms.validators import DataRequired, Optional

from app.controllers.seguridad import requiere_rol
from app.extensions import db
from app.models import (
    EstadoPedido, EstadoRuta, Pedido, Rol, Ruta, Usuario, Vehiculo,
)
from app.services.configuracion import centro_distribucion
from app.services.planificacion import (
    PlanificacionInvalida, crear_ruta, eliminar_ruta, recalcular_secuencia,
)
from app.services.planificacion import quitar_parada as quitar_parada_servicio
from app.services.validacion import DatosInvalidos
from app.services.ruteo import ESTRATEGIA_DISTANCIA, ESTRATEGIAS
from app.tiempo import hoy

rutas_bp = Blueprint("rutas", __name__)


class FormularioRuta(FlaskForm):
    fecha = DateField("Fecha de despacho", validators=[DataRequired("Indique la fecha.")])
    conductor_id = SelectField("Conductor", coerce=int, validators=[DataRequired()])
    vehiculo_id = SelectField("Vehiculo", coerce=int, validators=[Optional()])
    estrategia = SelectField(
        "Criterio de ordenamiento",
        choices=list(ESTRATEGIAS.items()),
        default=ESTRATEGIA_DISTANCIA,
    )

    def cargar_opciones(self):
        conductores = (
            db.session.query(Usuario)
            .filter(Usuario.rol == Rol.CONDUCTOR, Usuario.activo.is_(True))
            .order_by(Usuario.nombre)
            .all()
        )
        self.conductor_id.choices = [(c.id, c.nombre) for c in conductores]

        vehiculos = (
            db.session.query(Vehiculo)
            .filter(Vehiculo.activo.is_(True))
            .order_by(Vehiculo.placa)
            .all()
        )
        self.vehiculo_id.choices = [(0, "Sin asignar")] + [
            (v.id, f"{v.placa} — {v.tipo}") for v in vehiculos
        ]
        return conductores, vehiculos


# --------------------------------------------------------------------------
# Listado
# --------------------------------------------------------------------------

@rutas_bp.route("/")
@login_required
@requiere_rol(Rol.ADMIN, Rol.DESPACHADOR)
def lista():
    consulta = db.session.query(Ruta).options(
        joinedload(Ruta.conductor), joinedload(Ruta.vehiculo)
    )

    fecha_texto = request.args.get("fecha", "").strip()
    estado = request.args.get("estado", "").strip()

    if fecha_texto:
        try:
            consulta = consulta.filter(
                Ruta.fecha == datetime.strptime(fecha_texto, "%Y-%m-%d").date()
            )
        except ValueError:
            flash("La fecha del filtro no es valida.", "advertencia")

    if estado and estado in EstadoRuta.ETIQUETAS:
        consulta = consulta.filter(Ruta.estado == estado)

    rutas = consulta.order_by(Ruta.fecha.desc(), Ruta.codigo).all()

    return render_template(
        "rutas/lista.html", rutas=rutas, filtros={"fecha": fecha_texto, "estado": estado}
    )


# --------------------------------------------------------------------------
# Planificacion
# --------------------------------------------------------------------------

@rutas_bp.route("/nueva", methods=["GET", "POST"])
@login_required
@requiere_rol(Rol.ADMIN, Rol.DESPACHADOR)
def nueva():
    formulario = FormularioRuta()
    formulario.cargar_opciones()

    fecha_seleccionada = request.values.get("fecha", "")
    if fecha_seleccionada:
        try:
            fecha_seleccionada = datetime.strptime(fecha_seleccionada, "%Y-%m-%d").date()
        except ValueError:
            fecha_seleccionada = hoy()
    else:
        fecha_seleccionada = hoy()

    if not formulario.fecha.data:
        formulario.fecha.data = fecha_seleccionada

    # Solo pueden asignarse pedidos que aun no pertenecen a ninguna ruta.
    disponibles = (
        db.session.query(Pedido)
        .filter(
            Pedido.fecha_despacho == fecha_seleccionada,
            Pedido.estado == EstadoPedido.PENDIENTE,
            Pedido.ruta_id.is_(None),
        )
        .order_by(Pedido.prioridad, Pedido.ventana_fin, Pedido.codigo)
        .all()
    )

    if formulario.validate_on_submit():
        try:
            ruta, resultado = crear_ruta({
                "fecha": formulario.fecha.data,
                "conductor_id": formulario.conductor_id.data,
                "vehiculo_id": formulario.vehiculo_id.data or None,
                "estrategia": formulario.estrategia.data,
                "pedidos": request.form.getlist("pedido_id", type=int),
            }, current_user.id)
        except DatosInvalidos as error:
            db.session.rollback()
            for _, mensaje in error.errores:
                flash(mensaje, "error")
        else:
            db.session.commit()
            for advertencia in resultado.advertencias:
                flash(advertencia, "advertencia")
            flash(
                f"Ruta {ruta.codigo} creada con {len(ruta.pedidos)} parada(s): "
                f"{ruta.distancia_km} km estimados.",
                "exito",
            )
            return redirect(url_for("rutas.detalle", ruta_id=ruta.id))

    return render_template(
        "rutas/nueva.html",
        formulario=formulario,
        disponibles=disponibles,
        fecha_seleccionada=fecha_seleccionada,
    )


# --------------------------------------------------------------------------
# Detalle y operaciones
# --------------------------------------------------------------------------

@rutas_bp.route("/<int:ruta_id>")
@login_required
@requiere_rol(Rol.ADMIN, Rol.DESPACHADOR)
def detalle(ruta_id):
    ruta = db.session.get(Ruta, ruta_id)
    if ruta is None:
        abort(404)

    centro = centro_distribucion()
    datos_origen = {
        "lat": centro["latitud"],
        "lng": centro["longitud"],
        "nombre": centro["nombre"],
    }

    # Datos que consume el mapa; se serializan en el controlador para no
    # construir JSON dentro de la plantilla.
    paradas = [
        {
            "orden": pedido.orden_en_ruta or 0,
            "codigo": pedido.codigo,
            "cliente": pedido.cliente_nombre,
            "direccion": pedido.direccion,
            "ventana": pedido.ventana_texto,
            "estado": pedido.estado,
            "lat": pedido.latitud,
            "lng": pedido.longitud,
        }
        for pedido in ruta.pedidos
        if pedido.tiene_coordenadas
    ]

    return render_template(
        "rutas/detalle.html",
        ruta=ruta,
        origen=datos_origen,
        estrategias=ESTRATEGIAS,
        datos_mapa={
            "origen": datos_origen,
            "paradas": paradas,
            "geometria": json.loads(ruta.geometria) if ruta.geometria else None,
        },
    )


@rutas_bp.route("/<int:ruta_id>/recalcular", methods=["POST"])
@login_required
@requiere_rol(Rol.ADMIN, Rol.DESPACHADOR)
def recalcular(ruta_id):
    ruta = db.session.get(Ruta, ruta_id)
    if ruta is None:
        abort(404)

    estrategia = request.form.get("estrategia", ESTRATEGIA_DISTANCIA)
    if estrategia not in ESTRATEGIAS:
        estrategia = ESTRATEGIA_DISTANCIA

    try:
        resultado = recalcular_secuencia(ruta, estrategia)
    except PlanificacionInvalida as error:
        flash(str(error), "error")
        return redirect(url_for("rutas.detalle", ruta_id=ruta.id))

    if resultado is None:
        flash("No quedan paradas pendientes por reordenar.", "advertencia")
        return redirect(url_for("rutas.detalle", ruta_id=ruta.id))

    abiertos = [p for p in ruta.pedidos if p.estado in EstadoPedido.ABIERTOS]
    db.session.commit()

    for advertencia in resultado.advertencias:
        flash(advertencia, "advertencia")

    flash(
        f"Ruta recalculada: {len(abiertos)} parada(s) reordenadas, "
        f"{ruta.distancia_km} km estimados.",
        "exito",
    )
    return redirect(url_for("rutas.detalle", ruta_id=ruta.id))


@rutas_bp.route("/<int:ruta_id>/quitar/<int:pedido_id>", methods=["POST"])
@login_required
@requiere_rol(Rol.ADMIN, Rol.DESPACHADOR)
def quitar_parada(ruta_id, pedido_id):
    ruta = db.session.get(Ruta, ruta_id)
    pedido = db.session.get(Pedido, pedido_id)

    if ruta is None or pedido is None or pedido.ruta_id != ruta.id:
        abort(404)

    try:
        quitar_parada_servicio(ruta, pedido, current_user.id)
    except PlanificacionInvalida as error:
        flash(str(error), "error")
        return redirect(url_for("rutas.detalle", ruta_id=ruta.id))
    db.session.commit()

    flash(f"Pedido {pedido.codigo} devuelto a la bolsa de pendientes.", "exito")
    return redirect(url_for("rutas.detalle", ruta_id=ruta.id))


@rutas_bp.route("/<int:ruta_id>/eliminar", methods=["POST"])
@login_required
@requiere_rol(Rol.ADMIN, Rol.DESPACHADOR)
def eliminar(ruta_id):
    ruta = db.session.get(Ruta, ruta_id)
    if ruta is None:
        abort(404)

    try:
        codigo, _ = eliminar_ruta(ruta, current_user.id)
    except PlanificacionInvalida as error:
        flash(str(error), "error")
        return redirect(url_for("rutas.detalle", ruta_id=ruta.id))
    db.session.commit()

    flash(f"Ruta {codigo} eliminada; sus pedidos volvieron a pendientes.", "exito")
    return redirect(url_for("rutas.lista"))
