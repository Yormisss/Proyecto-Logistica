"""Configuracion del negocio (solo el administrador).

Centro de distribucion y catalogo de motivos de entrega fallida. Las reglas
viven en `app.services.configuracion`, que tambien usa el asistente de voz.
Cada cambio queda en la auditoria.
"""

from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from sqlalchemy import func
from wtforms import StringField
from wtforms.validators import Optional

from app.controllers.seguridad import requiere_rol
from app.extensions import db
from app.models import MotivoFallo, PruebaEntrega, Rol
from app.services import configuracion as servicio
from app.services.auditoria import registrar_auditoria

configuracion_bp = Blueprint("configuracion", __name__)


class FormularioCentro(FlaskForm):
    cd_nombre = StringField("Nombre", validators=[Optional()])
    cd_latitud = StringField("Latitud", validators=[Optional()])
    cd_longitud = StringField("Longitud", validators=[Optional()])


class FormularioMotivo(FlaskForm):
    nombre = StringField("Motivo", validators=[Optional()])


class FormularioAccion(FlaskForm):
    """Solo aporta el token CSRF."""


def _mostrar_errores(error):
    for _, mensaje in error.errores:
        flash(mensaje, "error")


def _motivo(motivo_id):
    motivo = db.session.get(MotivoFallo, motivo_id)
    if motivo is None:
        abort(404)
    return motivo


@configuracion_bp.route("/")
@login_required
@requiere_rol(Rol.ADMIN)
def inicio():
    centro = servicio.centro_distribucion()
    formulario_centro = FormularioCentro(
        cd_nombre=centro["nombre"], cd_latitud=centro["latitud"], cd_longitud=centro["longitud"]
    )
    usos = dict(
        db.session.query(PruebaEntrega.motivo_id, func.count(PruebaEntrega.id))
        .filter(PruebaEntrega.motivo_id.isnot(None))
        .group_by(PruebaEntrega.motivo_id)
        .all()
    )
    return render_template(
        "admin/configuracion.html",
        formulario_centro=formulario_centro,
        formulario_motivo=FormularioMotivo(),
        formulario_accion=FormularioAccion(),
        motivos=servicio.catalogo(incluir_inactivos=True),
        usos=usos,
        largo_maximo=servicio.LARGO_MAXIMO_MOTIVO,
    )


@configuracion_bp.route("/centro", methods=["POST"])
@login_required
@requiere_rol(Rol.ADMIN)
def centro():
    formulario = FormularioCentro()
    if not formulario.validate_on_submit():
        flash("Solicitud invalida. Intente nuevamente.", "error")
        return redirect(url_for("configuracion.inicio"))
    try:
        anterior, nuevo = servicio.editar_centro(
            formulario.cd_nombre.data, formulario.cd_latitud.data, formulario.cd_longitud.data,
            current_user.id,
        )
    except servicio.ConfiguracionInvalida as error:
        _mostrar_errores(error)
        return redirect(url_for("configuracion.inicio"))
    registrar_auditoria(
        "Editar centro de distribución", "configuracion",
        f"Actualizó el centro de distribución: {nuevo['nombre']}.",
        {clave: [anterior[clave], nuevo[clave]] for clave in ("nombre", "latitud", "longitud")},
    )
    db.session.commit()
    flash(f"Centro de distribucion actualizado: {nuevo['nombre']}. Aplica a las rutas nuevas "
          "o recalculadas.", "exito")
    return redirect(url_for("configuracion.inicio"))


@configuracion_bp.route("/motivos", methods=["POST"])
@login_required
@requiere_rol(Rol.ADMIN)
def agregar_motivo():
    formulario = FormularioMotivo()
    if not formulario.validate_on_submit():
        flash("Solicitud invalida. Intente nuevamente.", "error")
        return redirect(url_for("configuracion.inicio"))
    try:
        motivo = servicio.agregar_motivo(formulario.nombre.data)
    except servicio.ConfiguracionInvalida as error:
        _mostrar_errores(error)
        return redirect(url_for("configuracion.inicio"))
    db.session.flush()
    registrar_auditoria("Agregar motivo de fallo", "motivo_fallo",
                        f"Agregó el motivo de fallo «{motivo.nombre}».",
                        {"motivo": motivo.nombre}, entidad_id=motivo.id)
    db.session.commit()
    flash(f"Motivo '{motivo.nombre}' agregado.", "exito")
    return redirect(url_for("configuracion.inicio"))


@configuracion_bp.route("/motivos/<int:motivo_id>/renombrar", methods=["POST"])
@login_required
@requiere_rol(Rol.ADMIN)
def renombrar_motivo(motivo_id):
    motivo = _motivo(motivo_id)
    formulario = FormularioMotivo()
    if not formulario.validate_on_submit():
        flash("Solicitud invalida. Intente nuevamente.", "error")
        return redirect(url_for("configuracion.inicio"))
    try:
        anterior = servicio.renombrar_motivo(motivo, formulario.nombre.data)
    except servicio.ConfiguracionInvalida as error:
        _mostrar_errores(error)
        return redirect(url_for("configuracion.inicio"))
    registrar_auditoria("Renombrar motivo de fallo", "motivo_fallo",
                        f"Renombró el motivo de fallo «{anterior}» a «{motivo.nombre}».",
                        {"motivo": [anterior, motivo.nombre]}, entidad_id=motivo.id)
    db.session.commit()
    flash(f"Motivo '{anterior}' renombrado a '{motivo.nombre}'. Las entregas ya registradas "
          "conservan su texto.", "exito")
    return redirect(url_for("configuracion.inicio"))


@configuracion_bp.route("/motivos/<int:motivo_id>/estado", methods=["POST"])
@login_required
@requiere_rol(Rol.ADMIN)
def alternar_motivo(motivo_id):
    motivo = _motivo(motivo_id)
    if not FormularioAccion().validate_on_submit():
        flash("Solicitud invalida. Intente nuevamente.", "error")
        return redirect(url_for("configuracion.inicio"))
    try:
        servicio.cambiar_estado_motivo(motivo, not motivo.activo)
    except servicio.ConfiguracionInvalida as error:
        _mostrar_errores(error)
        return redirect(url_for("configuracion.inicio"))
    registrar_auditoria(
        "Activar motivo de fallo" if motivo.activo else "Desactivar motivo de fallo",
        "motivo_fallo",
        f"{'Activó' if motivo.activo else 'Desactivó'} el motivo de fallo «{motivo.nombre}».",
        {"motivo": motivo.nombre, "activo": [not motivo.activo, motivo.activo]},
        entidad_id=motivo.id,
    )
    db.session.commit()
    flash(f"Motivo '{motivo.nombre}' {'activado' if motivo.activo else 'desactivado'}.", "exito")
    return redirect(url_for("configuracion.inicio"))
