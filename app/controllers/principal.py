"""Controlador raiz: enruta al usuario segun su rol."""

from flask import Blueprint, redirect, url_for
from flask_login import current_user

from app.controllers.seguridad import destino_por_rol

principal_bp = Blueprint("principal", __name__)


@principal_bp.route("/")
def inicio():
    if not current_user.is_authenticated:
        return redirect(url_for("auth.login"))
    return redirect(destino_por_rol(current_user))


@principal_bp.route("/salud")
def salud():
    """Endpoint de verificacion para plataformas de despliegue (RNF3)."""
    return {"estado": "ok"}
