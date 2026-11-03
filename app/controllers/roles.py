"""Roles y su reparto de permisos (RF1 / RNF5). Solo los administradores.

Los cuatro roles del sistema siempre existen: el de administrador tiene todos
los permisos y el de cliente solo su portal, y no se editan; los de gestor
logistico y conductor si. Los demas los crea el administrador, se asignan a
las cuentas como roles adicionales (Usuarios > Permisos) y se pueden
eliminar. Las reglas viven en `app.services.permisos` y cada cambio queda en
la auditoria.
"""

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import login_required
from flask_wtf import FlaskForm
from wtforms import StringField
from wtforms.validators import Optional

from app.controllers.seguridad import requiere_permiso
from app.extensions import db
from app.models import RolAcceso
from app.permisos import ASIGNABLES, PERMISOS, nombre
from app.services import permisos as servicio
from app.services.auditoria import registrar_auditoria
from app.services.validacion import DatosInvalidos

roles_bp = Blueprint("roles", __name__)


class FormularioRol(FlaskForm):
    # Sin validadores propios: las reglas las aplica el servicio.
    nombre = StringField("Nombre del rol", validators=[Optional()])
    descripcion = StringField("Descripción", validators=[Optional()])


class FormularioAccion(FlaskForm):
    """Solo aporta el token CSRF."""


def _rol(rol_id):
    rol = db.session.get(RolAcceso, rol_id)
    if rol is None:
        abort(404)
    return rol


def _datos(formulario):
    return {"nombre": formulario.nombre.data, "descripcion": formulario.descripcion.data,
            "permisos": request.form.getlist("permisos")}


def _grupos(elegidos):
    """[(grupo, [(clave, nombre, elegido)])] de los permisos que se reparten."""
    grupos = {}
    for clave in ASIGNABLES:
        texto, grupo = PERMISOS[clave]
        grupos.setdefault(grupo, []).append((clave, texto, clave in elegidos))
    return list(grupos.items())


def _mostrar_errores(error):
    for _, mensaje in error.errores:
        flash(mensaje, "error")


@roles_bp.route("/")
@login_required
@requiere_permiso("roles.gestionar")
def lista():
    filas = [(rol, sorted(servicio.permisos_de(rol), key=list(PERMISOS).index),
              servicio.usuarios_con_rol(rol)) for rol in servicio.roles()]
    return render_template("admin/roles/lista.html", filas=filas, nombre_permiso=nombre,
                           formulario=FormularioAccion(), es_editable=servicio.es_editable)


@roles_bp.route("/nuevo", methods=["GET", "POST"])
@login_required
@requiere_permiso("roles.gestionar")
def nuevo():
    formulario = FormularioRol()
    elegidos = set(request.form.getlist("permisos"))
    if formulario.validate_on_submit():
        try:
            rol = servicio.crear_rol(_datos(formulario))
        except DatosInvalidos as error:
            _mostrar_errores(error)
        else:
            registrar_auditoria(
                "Crear rol", "rol",
                f"Creó el rol {rol.nombre} con {len(rol.asignaciones)} permiso(s).",
                {"rol": rol.nombre, "permisos": sorted(nombre(p) for p in rol.permisos)},
                entidad_id=rol.id,
            )
            db.session.commit()
            flash(f"Rol {rol.nombre} creado. Asígnelo a las cuentas en Usuarios > Permisos.", "exito")
            return redirect(url_for("roles.lista"))
    return render_template("admin/roles/formulario.html", formulario=formulario, rol=None,
                           grupos=_grupos(elegidos), editable=True)


@roles_bp.route("/<int:rol_id>/editar", methods=["GET", "POST"])
@login_required
@requiere_permiso("roles.gestionar")
def editar(rol_id):
    rol = _rol(rol_id)
    editable = servicio.es_editable(rol)
    formulario = FormularioRol(obj=rol)
    elegidos = (set(request.form.getlist("permisos")) if request.method == "POST"
                else set(servicio.permisos_de(rol)))
    if editable and formulario.validate_on_submit():
        try:
            cambios = servicio.editar_rol(rol, _datos(formulario))
        except DatosInvalidos as error:
            db.session.rollback()
            _mostrar_errores(error)
        else:
            if cambios:
                detalle = {campo: list(valores) for campo, valores in cambios.items()}
                if "permisos" in detalle:
                    detalle["permisos"] = [[nombre(p) for p in lista] for lista in detalle["permisos"]]
                registrar_auditoria(
                    "Editar rol", "rol",
                    f"Editó el rol {rol.nombre}: " + ", ".join(cambios) + ".",
                    detalle, entidad_id=rol.id,
                )
            db.session.commit()
            flash(f"Rol {rol.nombre} actualizado. Aplica desde la siguiente acción de cada cuenta.",
                  "exito")
            return redirect(url_for("roles.lista"))
    return render_template("admin/roles/formulario.html", formulario=formulario, rol=rol,
                           grupos=_grupos(elegidos), editable=editable,
                           usuarios=servicio.usuarios_con_rol(rol), permisos=servicio.permisos_de(rol),
                           nombre_permiso=nombre)


@roles_bp.route("/<int:rol_id>/eliminar", methods=["POST"])
@login_required
@requiere_permiso("roles.gestionar")
def eliminar(rol_id):
    rol = _rol(rol_id)
    if not FormularioAccion().validate_on_submit():
        flash("Solicitud invalida. Intente nuevamente.", "error")
        return redirect(url_for("roles.lista"))
    nombre_rol, rol_id = rol.nombre, rol.id
    try:
        afectados = servicio.eliminar_rol(rol)
    except DatosInvalidos as error:
        _mostrar_errores(error)
        return redirect(url_for("roles.lista"))
    registrar_auditoria(
        "Eliminar rol", "rol",
        f"Eliminó el rol {nombre_rol}" + (f", que tenían {len(afectados)} cuenta(s)." if afectados else "."),
        {"rol": nombre_rol, "cuentas": afectados}, entidad_id=rol_id,
    )
    db.session.commit()
    flash(f"Rol {nombre_rol} eliminado." + (f" {len(afectados)} cuenta(s) dejan de tenerlo."
                                           if afectados else ""), "exito")
    return redirect(url_for("roles.lista"))
