"""Controlador de autenticacion - RF1 Gestion de Usuarios y Roles."""

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField
from wtforms.validators import DataRequired, Length

from app.controllers.seguridad import campo_correo, destino_por_rol, es_redireccion_segura
from app.extensions import db
from app.models import Usuario
from app.services import acceso

auth_bp = Blueprint("auth", __name__)

# Lo unico que puede hacer una sesion con contrasena temporal: cambiarla o salir.
PERMITIDAS_CON_TEMPORAL = {"auth.perfil", "auth.logout", "static"}


class FormularioLogin(FlaskForm):
    correo = campo_correo(obligatorio="Ingrese su correo.")
    contrasena = PasswordField(
        "Contrasena", validators=[DataRequired("Ingrese su contrasena.")]
    )
    recordar = BooleanField("Mantener sesion iniciada")


class FormularioCambioContrasena(FlaskForm):
    contrasena_actual = PasswordField("Contrasena actual", validators=[DataRequired()])
    contrasena_nueva = PasswordField(
        "Nueva contrasena",
        validators=[DataRequired(), Length(min=8, message="Minimo 8 caracteres.")],
    )


def cambiar_contrasena_propia(usuario, actual, nueva):
    """Cambia la contrasena de quien tiene la sesion. Devuelve el error o None.

    Exige la actual aunque la sesion ya este autenticada: una sesion olvidada
    abierta no basta para apropiarse de la cuenta. Por lo mismo, los fallos
    cuentan para el bloqueo del login: si no, esa sesion permitiria adivinar
    la contrasena sin limite. El cambio incrementa la version de la credencial
    y cierra todas las sesiones; la que hizo el cambio se renueva para no
    expulsar a quien acaba de cambiarla.
    """
    espera = acceso.segundos_bloqueo(usuario.correo)
    if espera:
        return acceso.mensaje_bloqueo(espera)
    if not usuario.verificar_contrasena(actual):
        fallidos, espera = acceso.registrar_fallo(usuario.correo)
        if espera:
            return acceso.mensaje_bloqueo(espera)
        return acceso.mensaje_intento("La contrasena actual no es correcta.", fallidos)
    acceso.registrar_exito(usuario.correo)
    if usuario.verificar_contrasena(nueva):
        return "La nueva contrasena debe ser distinta de la actual."

    usuario.establecer_contrasena(nueva)
    db.session.commit()
    login_user(usuario)
    return None


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(destino_por_rol(current_user))

    formulario = FormularioLogin()
    if formulario.validate_on_submit():
        correo = formulario.correo.data.strip().lower()

        # RNF5: un correo bloqueado por intentos fallidos se rechaza sin
        # verificar la contrasena, aunque esta vez sea la correcta.
        espera = acceso.segundos_bloqueo(correo)
        if espera:
            flash(acceso.mensaje_bloqueo(espera), "error")
            return render_template("auth/login.html", formulario=formulario)

        usuario = db.session.query(Usuario).filter_by(correo=correo).first()

        if usuario is None or not usuario.verificar_contrasena(formulario.contrasena.data):
            # Mensaje generico: no revela si el correo existe (RNF5). El
            # contador tambien corre para los correos que no existen.
            fallidos, espera = acceso.registrar_fallo(correo)
            if espera:
                flash(acceso.mensaje_bloqueo(espera), "error")
            else:
                flash(acceso.mensaje_intento(
                    "Credenciales invalidas. Verifique sus datos.", fallidos), "error")
            return render_template("auth/login.html", formulario=formulario)

        acceso.registrar_exito(correo)

        if not usuario.activo:
            flash("Su cuenta se encuentra inactiva. Contacte al administrador.", "error")
            return render_template("auth/login.html", formulario=formulario)

        login_user(usuario, remember=formulario.recordar.data)
        flash(f"Bienvenido, {usuario.nombre}.", "exito")

        if usuario.debe_cambiar_contrasena:
            flash("Su contrasena es temporal: defina una propia para continuar.", "advertencia")
            return redirect(url_for("auth.perfil"))

        siguiente = request.args.get("next")
        if siguiente and es_redireccion_segura(siguiente):
            return redirect(siguiente)
        return redirect(destino_por_rol(usuario))

    return render_template("auth/login.html", formulario=formulario)


@auth_bp.before_app_request
def exigir_cambio_contrasena():
    """Una contrasena asignada por el administrador solo sirve para reemplazarla.

    Se revisa en cada peticion, no solo en el login: asi la exigencia que el
    administrador marca alcanza tambien a las sesiones ya abiertas.
    """
    if request.endpoint in PERMITIDAS_CON_TEMPORAL:
        return None
    if current_user.is_authenticated and current_user.debe_cambiar_contrasena:
        return redirect(url_for("auth.perfil"))
    return None


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Sesion cerrada correctamente.", "exito")
    return redirect(url_for("auth.login"))


@auth_bp.route("/perfil", methods=["GET", "POST"])
@login_required
def perfil():
    formulario = FormularioCambioContrasena()
    if formulario.validate_on_submit():
        usuario = current_user._get_current_object()
        era_temporal = usuario.debe_cambiar_contrasena
        error = cambiar_contrasena_propia(
            usuario, formulario.contrasena_actual.data, formulario.contrasena_nueva.data
        )
        if error:
            flash(error, "error")
        else:
            flash("Contrasena actualizada.", "exito")
            if era_temporal:
                return redirect(destino_por_rol(usuario))
            return redirect(url_for("auth.perfil"))

    return render_template("auth/perfil.html", formulario=formulario)
