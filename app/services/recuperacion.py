"""Recuperacion de contrasena: "Olvide mi contrasena" (RF1 / RNF5).

Quien olvido su contrasena pide un enlace con su correo. El enlace lleva un
token firmado con SECRET_KEY (itsdangerous) con la cuenta y su
`version_credencial`, y vence a los VIGENCIA_MINUTOS. Es de un solo uso sin
guardar nada: restablecer la contrasena incrementa la version y el mismo
enlace deja de servir. Tambien deja de servir si la contrasena cambio por otra
via despues de pedirlo.

Pedir el enlace no cambia nada: solo quien abre el correo cambia la
contrasena, asi que nadie puede dejar a otro sin acceso con solo escribir su
correo. La pantalla responde lo mismo exista o no la cuenta, para no revelar
cuales hay, y se envia como maximo un enlace por cuenta cada ESPERA_MINUTOS.

Entrega: el enlace va al correo de inicio de sesion por el webhook
MAKE_WEBHOOK_RESTABLECIMIENTO_URL y se arma con URL_PUBLICA, nunca con el
Host de la peticion: ese lo escribe el cliente, y un atacante podria hacerle
llegar a la victima un enlace valido hacia su propio dominio. En desarrollo,
sin ese webhook, el enlace se escribe en la consola del servidor.
"""

from datetime import timedelta

from flask import current_app, url_for
from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.extensions import db
from app.models import Auditoria, Usuario
from app.services import acceso, sesiones
from app.services.auditoria import registrar_auditoria
from app.services.notificaciones import aviso_restablecimiento, restablecimiento_configurado
from app.tiempo import ahora

SAL = "restablecer-contrasena"
VIGENCIA_MINUTOS = 30
ESPERA_MINUTOS = 5

ACCION_SOLICITUD = "Solicitar restablecimiento de contraseña"
ACCION_RESTABLECIMIENTO = "Restablecer contraseña por correo"


def _serializador():
    return URLSafeTimedSerializer(current_app.secret_key, salt=SAL)


def _url_publica():
    return (current_app.config.get("URL_PUBLICA") or "").strip().rstrip("/")


def por_correo():
    """Si el enlace se envia por correo: hace falta el webhook y URL_PUBLICA."""
    return restablecimiento_configurado() and bool(_url_publica())


def disponible():
    """Si hay como entregar el enlace: por correo, o en la consola en desarrollo."""
    return por_correo() or current_app.debug


def generar_token(usuario):
    return _serializador().dumps({"u": usuario.id, "v": usuario.version_credencial or 0})


def usuario_del_token(token):
    """La cuenta del enlace, o None si no es valido, vencio o ya se uso."""
    try:
        datos = _serializador().loads(token, max_age=VIGENCIA_MINUTOS * 60)
    except BadSignature:   # tambien SignatureExpired
        return None
    if not isinstance(datos, dict) or not isinstance(datos.get("u"), int):
        return None
    usuario = db.session.get(Usuario, datos["u"])
    if usuario is None or not usuario.activo:
        return None
    if datos.get("v") != (usuario.version_credencial or 0):
        return None
    return usuario


def _solicitud_reciente(usuario):
    desde = ahora() - timedelta(minutes=ESPERA_MINUTOS)
    return db.session.query(Auditoria.id).filter(
        Auditoria.usuario_id == usuario.id,
        Auditoria.accion == ACCION_SOLICITUD,
        Auditoria.registrado_en >= desde,
    ).first() is not None


def solicitar(correo):
    """Envia el enlace si `correo` es de una cuenta activa.

    No devuelve nada a proposito: quien llama responde lo mismo en todos los
    casos. Hace commit.
    """
    if not disponible():
        return
    usuario = db.session.query(Usuario).filter_by(correo=correo, activo=True).first()
    if usuario is None or _solicitud_reciente(usuario):
        return

    token = generar_token(usuario)
    # El enlace no va a la auditoria: quien lo tiene cambia la contrasena.
    registrar_auditoria(
        ACCION_SOLICITUD, "usuario",
        f"{usuario.nombre} pidió un enlace para restablecer su contraseña.",
        {"correo": usuario.correo}, entidad_id=usuario.id, usuario=usuario, avisar=False,
    )
    if por_correo():
        enlace = _url_publica() + url_for("auth.restablecer", token=token)
        aviso_restablecimiento(usuario, enlace, VIGENCIA_MINUTOS)
    else:
        current_app.logger.warning(
            "Desarrollo, sin MAKE_WEBHOOK_RESTABLECIMIENTO_URL: enlace para restablecer la "
            "contrasena de %s (vence en %s minutos): %s", usuario.correo, VIGENCIA_MINUTOS,
            url_for("auth.restablecer", token=token, _external=True),
        )
    db.session.commit()


def restablecer(usuario, nueva):
    """Fija la contrasena nueva desde el enlace. Devuelve el error o None. Hace commit.

    Incrementa la version de la credencial (el enlace y toda sesion abierta
    caducan) y reinicia el contador de intentos fallidos: quien abrio el
    correo demostro ser el dueno de la cuenta.
    """
    if usuario.verificar_contrasena(nueva):
        return "La nueva contrasena debe ser distinta de la actual."
    usuario.establecer_contrasena(nueva)
    sesiones.cerrar(usuario)
    registrar_auditoria(
        ACCION_RESTABLECIMIENTO, "usuario",
        f"{usuario.nombre} restableció su contraseña con el enlace enviado a su correo.",
        {"correo": usuario.correo}, entidad_id=usuario.id, usuario=usuario,
    )
    db.session.commit()
    acceso.registrar_exito(usuario.correo)
    return None
