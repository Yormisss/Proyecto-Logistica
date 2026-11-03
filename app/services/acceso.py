"""RNF5 - Bloqueo temporal del inicio de sesion tras intentos fallidos.

Tras `LOGIN_MAX_INTENTOS` contrasenas equivocadas seguidas para un mismo
correo, ese correo queda bloqueado `LOGIN_BLOQUEO_SEGUNDOS`: el login lo
rechaza aun con la contrasena correcta, que ni siquiera se verifica. Asi un
ataque de fuerza bruta queda limitado a unos pocos intentos por minuto.

El contador vuelve a cero al entrar con la contrasena correcta, al cumplirse
el bloqueo o tras `VIGENCIA` sin fallos. Aplica a todos los roles porque hay
un solo login, y tambien a la contrasena actual que pide el perfil: si no,
una sesion abierta permitiria adivinarla sin limite.
"""

import math
from datetime import timedelta

from flask import current_app
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import IntentoAcceso
from app.tiempo import ahora

# Un contador sin fallos durante este tiempo se descarta.
VIGENCIA = timedelta(days=1)


def maximo_intentos():
    return current_app.config["LOGIN_MAX_INTENTOS"]


def _segundos_restantes(fila, momento):
    if fila is None or fila.bloqueado_hasta is None:
        return 0
    restantes = math.ceil((fila.bloqueado_hasta - momento).total_seconds())
    return max(0, min(restantes, current_app.config["LOGIN_BLOQUEO_SEGUNDOS"]))


def segundos_bloqueo(correo):
    """Segundos que faltan para que el correo pueda intentarlo; 0 si ya puede."""
    return _segundos_restantes(db.session.get(IntentoAcceso, correo), ahora())


def registrar_fallo(correo):
    """Suma un intento fallido. Devuelve (fallidos, segundos de bloqueo o 0)."""
    try:
        return _registrar_fallo(correo)
    except IntegrityError:
        # Dos primeros fallos simultaneos del mismo correo: el otro ya inserto
        # la fila, asi que el segundo intento la encuentra y la actualiza.
        db.session.rollback()
        return _registrar_fallo(correo)


def _registrar_fallo(correo):
    # Al segundo: una columna DATETIME de MySQL redondea los microsegundos, y
    # sin esto el bloqueo recien creado anunciaba 61 segundos en vez de 1 minuto.
    momento = ahora().replace(microsecond=0)
    # "fetch" quita de la sesion las filas borradas: si no, una fila vieja ya
    # cargada por `segundos_bloqueo` chocaria con la nueva del mismo correo.
    db.session.query(IntentoAcceso).filter(
        IntentoAcceso.ultimo_fallo_en < momento - VIGENCIA
    ).delete(synchronize_session="fetch")

    # FOR UPDATE: dos fallos simultaneos no deben contar como uno (MySQL
    # bloquea la fila; SQLite serializa las escrituras por si solo).
    fila = (
        db.session.query(IntentoAcceso)
        .filter_by(correo=correo)
        .with_for_update()
        .populate_existing()
        .first()
    )
    if fila is None:
        fila = IntentoAcceso(correo=correo, fallidos=0)
        db.session.add(fila)
    elif fila.bloqueado_hasta is not None and fila.bloqueado_hasta <= momento:
        # Bloqueo cumplido: el correo vuelve a tener todos sus intentos.
        fila.fallidos, fila.bloqueado_hasta = 0, None

    fila.fallidos += 1
    fila.ultimo_fallo_en = momento
    if fila.fallidos >= maximo_intentos():
        # Desde el segundo siguiente al fallo: contado desde `momento` (truncado),
        # un reintento en el segundo siguiente anunciaba 59 segundos y no 1 minuto.
        # `_segundos_restantes` nunca anuncia mas que el bloqueo configurado.
        fila.bloqueado_hasta = momento + timedelta(
            seconds=current_app.config["LOGIN_BLOQUEO_SEGUNDOS"] + 1
        )
    db.session.commit()
    return fila.fallidos, _segundos_restantes(fila, momento)


def registrar_exito(correo):
    """La contrasena correcta reinicia el contador del correo."""
    db.session.query(IntentoAcceso).filter_by(correo=correo).delete(
        synchronize_session="fetch"
    )
    db.session.commit()


def resumen(correos):
    """{correo: (fallidos, segundos de bloqueo)} con los contadores vigentes.

    Un bloqueo ya cumplido se informa como sin fallos: el proximo intento
    empieza de cero (ver `_registrar_fallo`).
    """
    momento = ahora()
    filas = db.session.query(IntentoAcceso).filter(
        IntentoAcceso.correo.in_(list(correos)),
        IntentoAcceso.ultimo_fallo_en >= momento - VIGENCIA,
    )
    estado = {}
    for fila in filas:
        cumplido = fila.bloqueado_hasta is not None and fila.bloqueado_hasta <= momento
        if not cumplido:
            estado[fila.correo] = (fila.fallidos, _segundos_restantes(fila, momento))
    return estado


def mensaje_bloqueo(segundos):
    if segundos >= 60 and segundos % 60 == 0:
        minutos = segundos // 60
        espera = f"{minutos} minuto" + ("s" if minutos > 1 else "")
    else:
        espera = f"{segundos} segundo" + ("s" if segundos != 1 else "")
    return (
        f"Acceso no valido: se superaron los {maximo_intentos()} intentos permitidos. "
        f"Espere {espera} para volver a intentar iniciar sesion."
    )


def mensaje_intento(prefijo, fallidos):
    return f"{prefijo} Intento fallido {fallidos} de {maximo_intentos()}."
