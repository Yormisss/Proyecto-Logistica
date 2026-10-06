"""Solicitudes de contacto de los clientes (gestor logistico y admin)."""

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from flask_wtf import FlaskForm

from app.controllers.seguridad import requiere_rol
from app.extensions import db
from app.models import Rol, SolicitudContacto
from app.services.solicitudes import SolicitudInvalida, marcar_atendida

solicitudes_bp = Blueprint("solicitudes", __name__)

FILTROS = ("pendientes", "atendidas", "todas")


class FormularioAtender(FlaskForm):
    """Solo aporta el token CSRF."""


@solicitudes_bp.route("/")
@login_required
@requiere_rol(Rol.ADMIN, Rol.DESPACHADOR)
def lista():
    filtro = request.args.get("estado", "pendientes")
    filtro = filtro if filtro in FILTROS else "pendientes"

    consulta = db.session.query(SolicitudContacto)
    if filtro == "pendientes":
        consulta = consulta.filter(SolicitudContacto.atendida.is_(False)).order_by(
            SolicitudContacto.creada_en, SolicitudContacto.id
        )
    else:
        if filtro == "atendidas":
            consulta = consulta.filter(SolicitudContacto.atendida.is_(True))
        consulta = consulta.order_by(
            SolicitudContacto.creada_en.desc(), SolicitudContacto.id.desc()
        )

    return render_template(
        "admin/solicitudes.html",
        solicitudes=consulta.limit(200).all(),
        filtro=filtro,
        formulario=FormularioAtender(),
    )


@solicitudes_bp.route("/<int:solicitud_id>/atender", methods=["POST"])
@login_required
@requiere_rol(Rol.ADMIN, Rol.DESPACHADOR)
def atender(solicitud_id):
    solicitud = db.session.get(SolicitudContacto, solicitud_id)
    if solicitud is None:
        abort(404)
    if not FormularioAtender().validate_on_submit():
        flash("Solicitud invalida. Intente nuevamente.", "error")
        return redirect(url_for("solicitudes.lista"))
    try:
        marcar_atendida(solicitud, current_user.id)
    except SolicitudInvalida as error:
        flash(str(error), "advertencia")
        return redirect(url_for("solicitudes.lista"))
    db.session.commit()
    flash(f"Solicitud de {solicitud.cliente.nombre} marcada como atendida.", "exito")
    return redirect(url_for("solicitudes.lista"))
