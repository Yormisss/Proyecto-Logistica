"""Flota de transporte (admin y gestor logistico).

Las reglas viven en `app.services.vehiculos`, que tambien usa el asistente de voz.
"""

from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_login import login_required
from flask_wtf import FlaskForm
from wtforms import BooleanField, SelectField, StringField
from wtforms.validators import Optional

from app.controllers.seguridad import requiere_rol
from app.extensions import db
from app.models import Rol, Usuario, Vehiculo
from app.services.auditoria import auditar_pantalla, cambios_legibles, campos_cambiados
from app.services.validacion import DatosInvalidos
from app.services.vehiculos import (
    cambiar_estado_vehiculo, crear_vehiculo, editar_vehiculo, rutas_abiertas,
)

vehiculos_bp = Blueprint("vehiculos", __name__)


def _nombre_conductor(conductor_id):
    conductor = db.session.get(Usuario, conductor_id) if conductor_id else None
    return conductor.nombre if conductor else None


def _detalle_auditoria(cambios):
    """Los cambios con el conductor por su nombre, no por su id."""
    if "conductor_id" in cambios:
        antes, despues = cambios.pop("conductor_id")
        cambios["conductor"] = (_nombre_conductor(antes), _nombre_conductor(despues))
    return cambios_legibles(cambios)


class FormularioVehiculo(FlaskForm):
    placa = StringField("Placa", validators=[Optional()])
    tipo = StringField("Tipo", validators=[Optional()], default="Furgon")
    capacidad_kg = StringField("Capacidad (kg)", validators=[Optional()], default="0")
    capacidad_unidades = StringField("Capacidad (unidades)", validators=[Optional()], default="0")
    conductor_id = SelectField("Conductor asignado", coerce=int, validate_choice=False,
                               validators=[Optional()])
    activo = BooleanField("Vehículo activo", default=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        conductores = (
            db.session.query(Usuario)
            .filter(Usuario.rol == Rol.CONDUCTOR, Usuario.activo.is_(True))
            .order_by(Usuario.nombre)
            .all()
        )
        self.conductor_id.choices = [(0, "— Sin conductor —")] + [(c.id, c.nombre) for c in conductores]

    def datos(self):
        return {"placa": self.placa.data, "tipo": self.tipo.data, "capacidad_kg": self.capacidad_kg.data,
                "capacidad_unidades": self.capacidad_unidades.data,
                "conductor_id": self.conductor_id.data or None, "activo": self.activo.data}


class FormularioAccion(FlaskForm):
    """Solo aporta el token CSRF."""


def _mostrar_errores(formulario, error):
    for campo, mensaje in error.errores:
        destino = getattr(formulario, campo, None) if campo else None
        if destino is not None:
            destino.errors = list(destino.errors) + [mensaje]
        else:
            flash(mensaje, "error")


def _vehiculo(vehiculo_id):
    vehiculo = db.session.get(Vehiculo, vehiculo_id)
    if vehiculo is None:
        abort(404)
    return vehiculo


@vehiculos_bp.route("/")
@login_required
@requiere_rol(Rol.ADMIN, Rol.DESPACHADOR)
def lista():
    vehiculos = db.session.query(Vehiculo).order_by(Vehiculo.activo.desc(), Vehiculo.placa).all()
    return render_template("vehiculos/lista.html", vehiculos=vehiculos, formulario=FormularioAccion(),
                           abiertas={v.id: rutas_abiertas(v) for v in vehiculos})


@vehiculos_bp.route("/nuevo", methods=["GET", "POST"])
@login_required
@requiere_rol(Rol.ADMIN, Rol.DESPACHADOR)
def nuevo():
    formulario = FormularioVehiculo()
    if formulario.validate_on_submit():
        try:
            vehiculo = crear_vehiculo(formulario.datos())
        except DatosInvalidos as error:
            db.session.rollback()
            _mostrar_errores(formulario, error)
        else:
            auditar_pantalla("Crear vehículo", "vehiculo", f"Registró el vehículo {vehiculo.placa}.",
                             {"placa": vehiculo.placa, "tipo": vehiculo.tipo,
                              "capacidad_kg": vehiculo.capacidad_kg,
                              "capacidad_unidades": vehiculo.capacidad_unidades,
                              "conductor": _nombre_conductor(vehiculo.conductor_id),
                              "activo": vehiculo.activo},
                             entidad_id=vehiculo.id)
            db.session.commit()
            flash(f"Vehiculo {vehiculo.placa} registrado.", "exito")
            return redirect(url_for("vehiculos.lista"))
    return render_template("vehiculos/formulario.html", formulario=formulario, vehiculo=None)


@vehiculos_bp.route("/<int:vehiculo_id>/editar", methods=["GET", "POST"])
@login_required
@requiere_rol(Rol.ADMIN, Rol.DESPACHADOR)
def editar(vehiculo_id):
    vehiculo = _vehiculo(vehiculo_id)
    formulario = FormularioVehiculo(obj=vehiculo)
    if not formulario.is_submitted():
        formulario.conductor_id.data = vehiculo.conductor_id or 0
    if formulario.validate_on_submit():
        try:
            cambios = editar_vehiculo(vehiculo, formulario.datos())
        except DatosInvalidos as error:
            db.session.rollback()
            _mostrar_errores(formulario, error)
        else:
            if cambios:
                auditar_pantalla("Editar vehículo", "vehiculo",
                                 f"Editó el vehículo {vehiculo.placa}: {campos_cambiados(cambios)}.",
                                 _detalle_auditoria(cambios), entidad_id=vehiculo.id)
            db.session.commit()
            flash(f"Vehiculo {vehiculo.placa} actualizado.", "exito")
            return redirect(url_for("vehiculos.lista"))
    return render_template("vehiculos/formulario.html", formulario=formulario, vehiculo=vehiculo)


@vehiculos_bp.route("/<int:vehiculo_id>/estado", methods=["POST"])
@login_required
@requiere_rol(Rol.ADMIN, Rol.DESPACHADOR)
def alternar(vehiculo_id):
    vehiculo = _vehiculo(vehiculo_id)
    if not FormularioAccion().validate_on_submit():
        flash("Solicitud invalida. Intente nuevamente.", "error")
        return redirect(url_for("vehiculos.lista"))
    try:
        cambiar_estado_vehiculo(vehiculo, not vehiculo.activo)
    except DatosInvalidos as error:
        flash(str(error), "error")
        return redirect(url_for("vehiculos.lista"))
    auditar_pantalla("Activar vehículo" if vehiculo.activo else "Desactivar vehículo", "vehiculo",
                     f"{'Activó' if vehiculo.activo else 'Desactivó'} el vehículo {vehiculo.placa}.",
                     {"activo": [not vehiculo.activo, vehiculo.activo]}, entidad_id=vehiculo.id)
    db.session.commit()
    flash(f"Vehiculo {vehiculo.placa} {'activado' if vehiculo.activo else 'desactivado'}.", "exito")
    return redirect(url_for("vehiculos.lista"))
