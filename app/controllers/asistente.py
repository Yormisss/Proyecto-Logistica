"""Inicio de llamadas con el asistente de voz (Retell AI).

El navegador nunca habla con la API de Retell con credenciales propias: pide
aqui la llamada, autenticado con su sesion y su token CSRF, y recibe solo lo
necesario para unirse a ella.

Durante la llamada, el navegador consulta `latido` cada pocos segundos. Es una
peticion de la sesion como cualquier otra, asi que cuenta como actividad: una
conversacion larga sin tocar la pantalla no cierra la sesion por inactividad
ni lleva la ventana al login, lo que cortaria la llamada. Si la llamada ya no
esta autorizada (ver `app.services.asistente.motivo_rechazo`), el navegador
cuelga y muestra el motivo.
"""

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from app.extensions import db
from app.models import SesionAsistente
from app.services.asistente import (
    MOTIVO_VENCIDA,
    AsistenteNoDisponible,
    ErrorRetell,
    iniciar_llamada,
    validar_llamada,
)

asistente_bp = Blueprint("asistente", __name__)


@asistente_bp.route("/llamada", methods=["POST"])
@login_required
def llamada():
    try:
        datos = iniciar_llamada(current_user)
    except AsistenteNoDisponible:
        return jsonify(error="Su rol no tiene asistente de voz."), 403
    except ErrorRetell:
        return jsonify(
            error="No se pudo conectar con el asistente. Intente de nuevo en un momento."
        ), 502
    return jsonify(datos)


@asistente_bp.route("/latido")
@login_required
def latido():
    """{"vigente": bool, "mensaje": motivo}: si la llamada de esta sesion sigue autorizada."""
    call_id = request.args.get("call_id", "")
    propia = db.session.query(SesionAsistente.id).filter_by(
        call_id=call_id, usuario_id=current_user.id).first()
    if propia is None:
        return jsonify(vigente=False, mensaje=MOTIVO_VENCIDA)
    sesion, motivo = validar_llamada(call_id)
    return jsonify(vigente=sesion is not None, mensaje=motivo or "")
