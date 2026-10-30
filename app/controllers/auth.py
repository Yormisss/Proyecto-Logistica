"""Controlador de autenticacion - RF1 Gestion de Usuarios y Roles."""

from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField
from wtforms.validators import DataRequired

from app.controllers.seguridad import (
    campo_correo, destino_por_rol, es_redireccion_segura, politica_contrasena,
)
from app.extensions import db
from app.models import Usuario
from app.services import acceso, sesiones
from app.services.auditoria import registrar_auditoria
from app.services.configuracion import minutos_inactividad, texto_minutos

auth_bp = Blueprint("auth", __name__)

# Lo unico que puede hacer una sesion con contrasena temporal: cambiarla o salir.
PERMITIDAS_CON_TEMPORAL = {"auth.perfil", "auth.logout", "auth.estado_sesion", "static"}

# Peticiones que no cuentan como actividad: el aviso de inactividad pregunta
# cuanto le queda a la sesion sin alargarla.
SIN_ACTIVIDAD = {"auth.estado_sesion", "static"}


class FormularioLogin(FlaskForm):
    correo = campo_correo(obligatorio="Ingrese su correo.")
    contrasena = PasswordField(
        "Contraseña", validators=[DataRequired("Ingrese su contrasena.")]
    )
    recordar = BooleanField("Mantener sesión iniciada")


class FormularioCambioContrasena(FlaskForm):
    contrasena_actual = PasswordField("Contraseña actual", validators=[DataRequired()])
    contrasena_nueva = PasswordField(
        "Nueva contrasena", validators=[DataRequired(), politica_contrasena],
    )


class FormularioCambioCorreo(FlaskForm):
    correo_nuevo = campo_correo("Nuevo correo", obligatorio="Ingrese el nuevo correo.")
    # Otro nombre que el del formulario de contrasena: estan en la misma pagina.
    contrasena_correo = PasswordField(
        "Contraseña actual", validators=[DataRequired("Ingrese su contrasena actual.")]
    )


def _verificar_actual(usuario, actual):
    """None si `actual` es la contrasena de la cuenta; si no, el mensaje de error.

    Exige la actual aunque la sesion ya este autenticada: una sesion olvidada
    abierta no basta para apropiarse de la cuenta. Por lo mismo, los fallos
    cuentan para el bloqueo del login: si no, esa sesion permitiria adivinar
    la contrasena sin limite.
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
    return None


def cambiar_contrasena_propia(usuario, actual, nueva):
    """Cambia la contrasena de quien tiene la sesion. Devuelve el error o None.

    El cambio incrementa la version de la credencial y cierra todas las
    sesiones; la que hizo el cambio se renueva para no expulsar a quien acaba
    de cambiarla.
    """
    error = _verificar_actual(usuario, actual)
    if error:
        return error
    if usuario.verificar_contrasena(nueva):
        return "La nueva contrasena debe ser distinta de la actual."

    usuario.establecer_contrasena(nueva)
    db.session.commit()
    login_user(usuario)
    return None


def cambiar_correo_propio(usuario, actual, nuevo):
    """Cambia el correo de inicio de sesion de quien tiene la sesion. Devuelve
    el error o None.

    Pide la contrasena actual, como el cambio de contrasena. El formulario ya
    aplico la lista blanca; aqui se revisa que el correo cambie y que no sea
    de otra cuenta. La sesion sigue abierta: su identificador no usa el correo.
    """
    error = _verificar_actual(usuario, actual)
    if error:
        return error
    nuevo = nuevo.strip().lower()
    if nuevo == usuario.correo:
        return "El nuevo correo es el mismo que ya tiene."
    if db.session.query(Usuario).filter(Usuario.correo == nuevo, Usuario.id != usuario.id).first():
        return "Ya existe una cuenta con este correo."

    anterior = usuario.correo
    usuario.correo = nuevo
    registrar_auditoria(
        "Cambiar correo propio", "usuario",
        f"{usuario.nombre} cambió su correo de {anterior} a {nuevo}.",
        {"correo": [anterior, nuevo]}, entidad_id=usuario.id,
    )
    db.session.commit()
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

        # RNF5 - Sesion unica: el token nuevo cierra la sesion de otro navegador.
        sesiones.iniciar(usuario)
        db.session.commit()
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
def vigilar_sesion():
    """Cierra la sesion por inactividad y avisa si otro navegador la cerro (RNF5).

    Ver app/services/sesiones.py. Va antes de `exigir_cambio_contrasena`: una
    sesion vencida se cierra aunque su contrasena sea temporal.
    """
    if request.endpoint in SIN_ACTIVIDAD:
        return None

    if current_user.is_authenticated:
        usuario = current_user._get_current_object()
        minutos = minutos_inactividad()
        if sesiones.segundos_restantes(usuario, minutos * 60) == 0:
            sesiones.cerrar(usuario)
            db.session.commit()
            logout_user()
            flash(f"Su sesion se cerro tras {texto_minutos(minutos)} sin actividad. "
                  "Inicie sesion de nuevo.", "advertencia")
            return redirect(url_for("auth.login"))
        sesiones.registrar_actividad(usuario)
        g.segundos_inactividad = minutos * 60
        return None

    # `cargar_usuario` rechazo la cookie pero su identificador sigue en ella.
    motivo = sesiones.motivo_cierre(session.get("_user_id"))
    if motivo:
        logout_user()
        flash(motivo, "advertencia")
        if request.endpoint != "auth.login":
            return redirect(url_for("auth.login"))
    return None


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


@auth_bp.route("/sesion")
def estado_sesion():
    """Segundos que le quedan a la sesion; lo consulta static/js/sesion.js."""
    if not current_user.is_authenticated:
        return {"restante": 0}
    return {"restante": sesiones.segundos_restantes(current_user)}


@auth_bp.route("/logout")
@login_required
def logout():
    sesiones.cerrar(current_user)
    db.session.commit()
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

    return render_template("auth/perfil.html", formulario=formulario,
                           formulario_correo=FormularioCambioCorreo(formdata=None))


@auth_bp.route("/perfil/correo", methods=["POST"])
@login_required
def cambiar_correo():
    formulario_correo = FormularioCambioCorreo()
    if formulario_correo.validate_on_submit():
        error = cambiar_correo_propio(
            current_user._get_current_object(), formulario_correo.contrasena_correo.data,
            formulario_correo.correo_nuevo.data,
        )
        if error:
            flash(error, "error")
        else:
            flash(f"Correo actualizado. Desde ahora inicia sesion con {current_user.correo}.",
                  "exito")
            return redirect(url_for("auth.perfil"))

    return render_template("auth/perfil.html", formulario=FormularioCambioContrasena(formdata=None),
                           formulario_correo=formulario_correo)
