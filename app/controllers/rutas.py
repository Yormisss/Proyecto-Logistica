"""RF3 - Planificacion y generacion de rutas de distribucion.

Sustituye la asignacion empirica, estatica y manual de rutas identificada en el
diagrama de Ishikawa (categoria Metodos) por una secuencia calculada a partir de
un servicio de geolocalizacion.
"""

import json
from datetime import date, datetime

from flask import (
    Blueprint, abort, current_app, flash, redirect, render_template, request, url_for,
)
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from sqlalchemy.orm import joinedload
from wtforms import DateField, SelectField
from wtforms.validators import DataRequired, Optional

from app.controllers.seguridad import requiere_rol
from app.extensions import db
from app.models import (
    EstadoPedido, EstadoRuta, EventoPedido, Pedido, Rol, Ruta, Usuario, Vehiculo,
)
from app.services.codigos import generar_codigo_ruta
from app.services.ruteo import ESTRATEGIA_DISTANCIA, ESTRATEGIAS, calcular_ruta

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


def _origen_centro_distribucion():
    return (current_app.config["CD_LAT"], current_app.config["CD_LNG"])


def _aplicar_resultado(ruta, resultado):
    """Persiste la secuencia calculada sobre los pedidos de la ruta."""
    posiciones = {pedido_id: indice for indice, pedido_id in enumerate(resultado.orden, start=1)}

    for pedido in ruta.pedidos:
        pedido.orden_en_ruta = posiciones.get(pedido.id)

    ruta.distancia_km = resultado.distancia_km
    ruta.duracion_min = resultado.duracion_min
    ruta.geometria = resultado.geometria
    ruta.proveedor_ruteo = resultado.proveedor


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
            fecha_seleccionada = date.today()
    else:
        fecha_seleccionada = date.today()

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
        seleccionados = request.form.getlist("pedido_id", type=int)

        if not seleccionados:
            flash("Seleccione al menos un pedido para la ruta.", "error")
            return render_template(
                "rutas/nueva.html", formulario=formulario,
                disponibles=disponibles, fecha_seleccionada=fecha_seleccionada,
            )

        pedidos = (
            db.session.query(Pedido)
            .filter(
                Pedido.id.in_(seleccionados),
                Pedido.ruta_id.is_(None),
                Pedido.estado == EstadoPedido.PENDIENTE,
            )
            .all()
        )

        if len(pedidos) != len(seleccionados):
            flash(
                "Algunos pedidos ya fueron asignados a otra ruta. Verifique la seleccion.",
                "error",
            )
            return redirect(url_for("rutas.nueva", fecha=formulario.fecha.data.isoformat()))

        ruta = Ruta(
            codigo=generar_codigo_ruta(formulario.fecha.data),
            fecha=formulario.fecha.data,
            estado=EstadoRuta.PLANIFICADA,
            conductor_id=formulario.conductor_id.data,
            vehiculo_id=formulario.vehiculo_id.data or None,
        )
        db.session.add(ruta)
        db.session.flush()

        for pedido in pedidos:
            pedido.ruta_id = ruta.id
            pedido.estado = EstadoPedido.ASIGNADO
            db.session.add(
                EventoPedido(
                    pedido_id=pedido.id,
                    usuario_id=current_user.id,
                    estado_anterior=EstadoPedido.PENDIENTE,
                    estado_nuevo=EstadoPedido.ASIGNADO,
                    nota=f"Asignado a la ruta {ruta.codigo}",
                )
            )

        resultado = calcular_ruta(
            _origen_centro_distribucion(), pedidos, formulario.estrategia.data
        )
        _aplicar_resultado(ruta, resultado)
        db.session.commit()

        for advertencia in resultado.advertencias:
            flash(advertencia, "advertencia")

        flash(
            f"Ruta {ruta.codigo} creada con {len(pedidos)} parada(s): "
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

    origen = _origen_centro_distribucion()
    datos_origen = {
        "lat": origen[0],
        "lng": origen[1],
        "nombre": current_app.config["CD_NOMBRE"],
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

    if ruta.estado == EstadoRuta.FINALIZADA:
        flash("No se puede recalcular una ruta finalizada.", "error")
        return redirect(url_for("rutas.detalle", ruta_id=ruta.id))

    # Las paradas ya cerradas conservan su posicion; solo se reordena lo pendiente.
    abiertos = [p for p in ruta.pedidos if p.estado in EstadoPedido.ABIERTOS]
    if not abiertos:
        flash("No quedan paradas pendientes por reordenar.", "advertencia")
        return redirect(url_for("rutas.detalle", ruta_id=ruta.id))

    estrategia = request.form.get("estrategia", ESTRATEGIA_DISTANCIA)
    if estrategia not in ESTRATEGIAS:
        estrategia = ESTRATEGIA_DISTANCIA

    resultado = calcular_ruta(_origen_centro_distribucion(), abiertos, estrategia)

    cerrados = [p for p in ruta.pedidos if p.estado in EstadoPedido.CERRADOS]
    posiciones = {pedido_id: indice for indice, pedido_id in enumerate(resultado.orden, start=1)}
    desplazamiento = len(cerrados)

    for indice, pedido in enumerate(cerrados, start=1):
        pedido.orden_en_ruta = indice
    for pedido in abiertos:
        pedido.orden_en_ruta = posiciones.get(pedido.id, 0) + desplazamiento

    ruta.distancia_km = resultado.distancia_km
    ruta.duracion_min = resultado.duracion_min
    ruta.geometria = resultado.geometria
    ruta.proveedor_ruteo = resultado.proveedor
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

    if pedido.estado in EstadoPedido.CERRADOS:
        flash("No se puede retirar una parada ya cerrada.", "error")
        return redirect(url_for("rutas.detalle", ruta_id=ruta.id))

    pedido.ruta_id = None
    pedido.orden_en_ruta = None
    pedido.estado = EstadoPedido.PENDIENTE
    db.session.add(
        EventoPedido(
            pedido_id=pedido.id,
            usuario_id=current_user.id,
            estado_anterior=EstadoPedido.ASIGNADO,
            estado_nuevo=EstadoPedido.PENDIENTE,
            nota=f"Retirado de la ruta {ruta.codigo}",
        )
    )
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

    if any(p.estado in EstadoPedido.CERRADOS for p in ruta.pedidos):
        flash(
            "No se puede eliminar una ruta que ya tiene entregas registradas.", "error"
        )
        return redirect(url_for("rutas.detalle", ruta_id=ruta.id))

    codigo = ruta.codigo
    for pedido in ruta.pedidos:
        pedido.ruta_id = None
        pedido.orden_en_ruta = None
        pedido.estado = EstadoPedido.PENDIENTE

    db.session.delete(ruta)
    db.session.commit()

    flash(f"Ruta {codigo} eliminada; sus pedidos volvieron a pendientes.", "exito")
    return redirect(url_for("rutas.lista"))
