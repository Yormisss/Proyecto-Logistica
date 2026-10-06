"""Inicio de llamadas con el asistente de voz (Retell AI).

El navegador nunca habla con la API de Retell con credenciales propias: pide
aqui la llamada, autenticado con su sesion y su token CSRF, y recibe solo lo
necesario para unirse a ella.
"""

from flask import Blueprint, jsonify
from flask_login import current_user, login_required

from app.services.asistente import (
    AsistenteNoDisponible,
    ErrorRetell,
    iniciar_llamada,
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
