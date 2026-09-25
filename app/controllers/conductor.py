"""RF4 - Interfaz de la flota de transporte.

Vista mobile-first (RNF1) donde el conductor consulta su ruta asignada, actualiza
el estado de cada pedido en terreno y registra la prueba de entrega (PoD).
"""

from flask import (
    Blueprint, abort, flash, redirect, render_template, request, url_for,
)
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from sqlalchemy.orm import joinedload
from wtforms import HiddenField, StringField, TextAreaField
from wtforms.validators import Length, Optional

from app.controllers.seguridad import requiere_rol
from app.extensions import db
from app.models import EstadoPedido, EstadoRuta, Pedido, Rol, Ruta
from app.services.despacho import TransicionInvalida, cambiar_estado, iniciar_ruta
from app.tiempo import hoy

conductor_bp = Blueprint("conductor", __name__)

MOTIVOS_FALLO = [
    "Cliente ausente",
    "Direccion incorrecta",
    "Establecimiento cerrado",
    "Cliente rechaza el pedido",
    "Zona de dificil acceso",
    "Fuera de la ventana horaria",
    "Novedad con la mercancia",
]


class FormularioEntrega(FlaskForm):
    """Prueba de entrega (PoD) - numeral 1.4 Alcances."""

    receptor_nombre = StringField("Recibido por", validators=[Optional(), Length(max=160)])
    receptor_documento = StringField("Documento", validators=[Optional(), Length(max=40)])
    observacion = TextAreaField("Observaciones", validators=[Optional(), Length(max=1000)])
    latitud = HiddenField()
    longitud = HiddenField()


class FormularioFallo(FlaskForm):
    motivo_fallo = StringField("Motivo", validators=[Optional(), Length(max=160)])
    observacion = TextAreaField("Detalle", validators=[Optional(), Length(max=1000)])
    latitud = HiddenField()
    longitud = HiddenField()


class FormularioAccion(FlaskForm):
    """Acciones sin datos adicionales; solo aporta el token CSRF."""

    latitud = HiddenField()
    longitud = HiddenField()


def _coordenada(valor, limite):
    """Convierte una coordenada enviada por el navegador.

    El campo puede llegar vacio (el usuario nego el permiso de ubicacion) o con
    un valor fuera de rango; en ambos casos se descarta sin interrumpir la
    actualizacion del estado, que es lo realmente critico en terreno.
    """
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    return numero if -limite <= numero <= limite else None


def _latitud(formulario):
    return _coordenada(formulario.latitud.data, 90)


def _longitud(formulario):
    return _coordenada(formulario.longitud.data, 180)


def _ruta_del_dia(fecha=None):
    fecha = fecha or hoy()
    return (
        db.session.query(Ruta)
        .options(joinedload(Ruta.pedidos))
        .filter(
            Ruta.conductor_id == current_user.id,
            Ruta.fecha == fecha,
            Ruta.estado.in_((EstadoRuta.PLANIFICADA, EstadoRuta.EN_CURSO)),
        )
        .order_by(Ruta.creada_en.desc())
        .first()
    )


def _pedido_del_conductor(pedido_id):
    """Garantiza que el conductor solo opere sobre sus propias paradas (RNF5)."""
    pedido = db.session.get(Pedido, pedido_id)
    if pedido is None:
        abort(404)
    if pedido.ruta is None or pedido.ruta.conductor_id != current_user.id:
        abort(403)
    return pedido


# --------------------------------------------------------------------------
# Ruta del dia
# --------------------------------------------------------------------------

@conductor_bp.route("/")
@login_required
@requiere_rol(Rol.CONDUCTOR)
def mi_ruta():
    fecha_hoy = hoy()
    ruta = _ruta_del_dia(fecha_hoy)

    return render_template(
        "conductor/mi_ruta.html",
        ruta=ruta,
        hoy=fecha_hoy,
        formulario=FormularioAccion(),
    )


@conductor_bp.route("/ruta/<int:ruta_id>/iniciar", methods=["POST"])
@login_required
@requiere_rol(Rol.CONDUCTOR)
def iniciar(ruta_id):
    ruta = db.session.get(Ruta, ruta_id)
    if ruta is None:
        abort(404)
    if ruta.conductor_id != current_user.id:
        abort(403)

    formulario = FormularioAccion()
    if not formulario.validate_on_submit():
        flash("Solicitud invalida. Intente nuevamente.", "error")
        return redirect(url_for("conductor.mi_ruta"))

    afectados, advertencias = iniciar_ruta(ruta, current_user.id)
    db.session.commit()

    for advertencia in advertencias:
        flash(advertencia, "advertencia")

    if afectados:
        flash(f"Ruta iniciada: {len(afectados)} parada(s) en camino.", "exito")
    else:
        flash("No habia paradas pendientes por iniciar.", "advertencia")

    return redirect(url_for("conductor.mi_ruta"))


# --------------------------------------------------------------------------
# Parada individual
# --------------------------------------------------------------------------

@conductor_bp.route("/parada/<int:pedido_id>")
@login_required
@requiere_rol(Rol.CONDUCTOR)
def parada(pedido_id):
    pedido = _pedido_del_conductor(pedido_id)

    return render_template(
        "conductor/parada.html",
        pedido=pedido,
        formulario_entrega=FormularioEntrega(),
        formulario_fallo=FormularioFallo(),
        formulario_accion=FormularioAccion(),
        motivos=MOTIVOS_FALLO,
    )


@conductor_bp.route("/parada/<int:pedido_id>/en-ruta", methods=["POST"])
@login_required
@requiere_rol(Rol.CONDUCTOR)
def marcar_en_ruta(pedido_id):
    pedido = _pedido_del_conductor(pedido_id)
    formulario = FormularioAccion()

    if not formulario.validate_on_submit():
        flash("Solicitud invalida. Intente nuevamente.", "error")
        return redirect(url_for("conductor.parada", pedido_id=pedido.id))

    try:
        cambiar_estado(
            pedido,
            EstadoPedido.EN_RUTA,
            current_user.id,
            nota="El conductor inicio el desplazamiento",
            latitud=_latitud(formulario),
            longitud=_longitud(formulario),
        )
    except TransicionInvalida as error:
        flash(str(error), "error")
        return redirect(url_for("conductor.parada", pedido_id=pedido.id))

    db.session.commit()
    flash(f"Pedido {pedido.codigo} marcado en ruta.", "exito")
    return redirect(url_for("conductor.parada", pedido_id=pedido.id))


@conductor_bp.route("/parada/<int:pedido_id>/entregar", methods=["POST"])
@login_required
@requiere_rol(Rol.CONDUCTOR)
def entregar(pedido_id):
    """RF4 + RF5: confirma la entrega y dispara el descuento de inventario."""
    pedido = _pedido_del_conductor(pedido_id)
    formulario = FormularioEntrega()

    if not formulario.validate_on_submit():
        # Los mensajes de WTForms llegan en ingles; se unifica el aviso al idioma
        # de la interfaz para no confundir al conductor en terreno.
        flash("No se pudo registrar la entrega. Recargue la pagina e intente de nuevo.", "error")
        return redirect(url_for("conductor.parada", pedido_id=pedido.id))

    try:
        resultado = cambiar_estado(
            pedido,
            EstadoPedido.ENTREGADO,
            current_user.id,
            nota="Entrega confirmada en terreno",
            latitud=_latitud(formulario),
            longitud=_longitud(formulario),
            receptor_nombre=formulario.receptor_nombre.data or None,
            receptor_documento=formulario.receptor_documento.data or None,
            observacion=formulario.observacion.data or None,
        )
    except TransicionInvalida as error:
        flash(str(error), "error")
        return redirect(url_for("conductor.parada", pedido_id=pedido.id))

    db.session.commit()

    for advertencia in resultado.advertencias:
        flash(advertencia, "advertencia")

    unidades = sum(m.cantidad for m in resultado.movimientos)
    if unidades:
        flash(
            f"Entrega registrada. Se descontaron {unidades} unidad(es) del inventario.",
            "exito",
        )
    else:
        flash("Entrega registrada.", "exito")

    return redirect(url_for("conductor.mi_ruta"))


@conductor_bp.route("/parada/<int:pedido_id>/fallar", methods=["POST"])
@login_required
@requiere_rol(Rol.CONDUCTOR)
def fallar(pedido_id):
    pedido = _pedido_del_conductor(pedido_id)
    formulario = FormularioFallo()

    if not formulario.validate_on_submit():
        flash("Solicitud invalida. Intente nuevamente.", "error")
        return redirect(url_for("conductor.parada", pedido_id=pedido.id))

    motivo = (formulario.motivo_fallo.data or "").strip()
    if not motivo:
        flash("Indique el motivo por el cual no se pudo entregar.", "error")
        return redirect(url_for("conductor.parada", pedido_id=pedido.id))

    try:
        cambiar_estado(
            pedido,
            EstadoPedido.FALLIDO,
            current_user.id,
            nota=f"Entrega fallida: {motivo}",
            latitud=_latitud(formulario),
            longitud=_longitud(formulario),
            motivo_fallo=motivo,
            observacion=formulario.observacion.data or None,
        )
    except TransicionInvalida as error:
        flash(str(error), "error")
        return redirect(url_for("conductor.parada", pedido_id=pedido.id))

    db.session.commit()
    flash(f"Pedido {pedido.codigo} registrado como fallido. El inventario no se afecto.", "exito")
    return redirect(url_for("conductor.mi_ruta"))


@conductor_bp.route("/parada/<int:pedido_id>/reintentar", methods=["POST"])
@login_required
@requiere_rol(Rol.CONDUCTOR)
def reintentar(pedido_id):
    """Permite volver a intentar una entrega que habia fallado."""
    pedido = _pedido_del_conductor(pedido_id)
    formulario = FormularioAccion()

    if not formulario.validate_on_submit():
        flash("Solicitud invalida. Intente nuevamente.", "error")
        return redirect(url_for("conductor.parada", pedido_id=pedido.id))

    try:
        cambiar_estado(
            pedido,
            EstadoPedido.EN_RUTA,
            current_user.id,
            nota="Reintento de entrega",
            latitud=_latitud(formulario),
            longitud=_longitud(formulario),
        )
    except TransicionInvalida as error:
        flash(str(error), "error")
        return redirect(url_for("conductor.parada", pedido_id=pedido.id))

    db.session.commit()
    flash(f"Pedido {pedido.codigo} en reintento de entrega.", "exito")
    return redirect(url_for("conductor.parada", pedido_id=pedido.id))


# --------------------------------------------------------------------------
# Historial
# --------------------------------------------------------------------------

@conductor_bp.route("/historial")
@login_required
@requiere_rol(Rol.CONDUCTOR)
def historial():
    rutas = (
        db.session.query(Ruta)
        .filter(Ruta.conductor_id == current_user.id)
        .order_by(Ruta.fecha.desc(), Ruta.codigo.desc())
        .limit(30)
        .all()
    )
    return render_template("conductor/historial.html", rutas=rutas)
