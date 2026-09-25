"""Controlador de autenticacion - RF1 Gestion de Usuarios y Roles."""

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, StringField
from wtforms.validators import DataRequired, Email, Length

from app.controllers.seguridad import destino_por_rol, es_redireccion_segura
from app.extensions import db
from app.models import Usuario

auth_bp = Blueprint("auth", __name__)


class FormularioLogin(FlaskForm):
    correo = StringField(
        "Correo electronico",
        validators=[DataRequired("Ingrese su correo."), Email("Correo no valido.")],
    )
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


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(destino_por_rol(current_user))

    formulario = FormularioLogin()
    if formulario.validate_on_submit():
        correo = formulario.correo.data.strip().lower()
        usuario = db.session.query(Usuario).filter_by(correo=correo).first()

        if usuario is None or not usuario.verificar_contrasena(formulario.contrasena.data):
            # Mensaje generico: no revela si el correo existe (RNF5).
            flash("Credenciales invalidas. Verifique sus datos.", "error")
            return render_template("auth/login.html", formulario=formulario)

        if not usuario.activo:
            flash("Su cuenta se encuentra inactiva. Contacte al administrador.", "error")
            return render_template("auth/login.html", formulario=formulario)

        login_user(usuario, remember=formulario.recordar.data)
        flash(f"Bienvenido, {usuario.nombre}.", "exito")

        siguiente = request.args.get("next")
        if siguiente and es_redireccion_segura(siguiente):
            return redirect(siguiente)
        return redirect(destino_por_rol(usuario))

    return render_template("auth/login.html", formulario=formulario)


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
        if not current_user.verificar_contrasena(formulario.contrasena_actual.data):
            flash("La contrasena actual no es correcta.", "error")
        else:
            current_user.establecer_contrasena(formulario.contrasena_nueva.data)
            db.session.commit()
            flash("Contrasena actualizada.", "exito")
            return redirect(url_for("auth.perfil"))

    return render_template("auth/perfil.html", formulario=formulario)
