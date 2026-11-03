"""Controlador del modulo de Big Data.

Los resultados del pipeline de Spark se muestran en el tablero de control
(`admin.dashboard`); esta ruta se conserva para no romper enlaces anteriores.
"""

from flask import Blueprint, redirect, url_for
from flask_login import login_required

from app.controllers.seguridad import requiere_permiso

bigdata_bp = Blueprint("bigdata", __name__)


@bigdata_bp.route("/")
@login_required
@requiere_permiso("bigdata.ver")
def panel():
    return redirect(url_for("admin.dashboard") + "#big-data")
