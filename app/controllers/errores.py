"""Registro de errores: consulta de los error logs (solo el administrador).

Solo lectura, del error mas reciente al mas antiguo. Lo escribe
`app.services.errores` con cada excepcion no controlada. Incluye una accion
para provocar un error a proposito y comprobar que el registro lo captura.
"""

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from flask_wtf import FlaskForm

from app.controllers.seguridad import requiere_rol
from app.extensions import db
from app.models import ErrorSistema, Rol

errores_bp = Blueprint("errores", __name__)

POR_PAGINA = 50


class FalloForzado(RuntimeError):
    """El error que provoca a proposito "Probar el registro"."""


class FormularioAccion(FlaskForm):
    """Solo aporta el token CSRF."""


@errores_bp.route("/")
@login_required
@requiere_rol(Rol.ADMIN)
def lista():
    total = db.session.query(ErrorSistema).count()
    paginas = max((total + POR_PAGINA - 1) // POR_PAGINA, 1)
    pagina = min(max(request.args.get("pagina", 1, type=int), 1), paginas)
    registros = (
        db.session.query(ErrorSistema)
        .order_by(ErrorSistema.id.desc())
        .offset((pagina - 1) * POR_PAGINA)
        .limit(POR_PAGINA)
        .all()
    )
    return render_template(
        "admin/errores.html", registros=registros, total=total, pagina=pagina,
        paginas=paginas, formulario=FormularioAccion(),
    )


@errores_bp.route("/probar", methods=["POST"])
@login_required
@requiere_rol(Rol.ADMIN)
def probar():
    """Provoca un error no controlado: la peticion termina en la pagina 500 y
    el registro debe mostrarlo en esta pantalla, con el mismo numero."""
    if not FormularioAccion().validate_on_submit():
        flash("Solicitud invalida. Intente nuevamente.", "error")
        return redirect(url_for("errores.lista"))
    raise FalloForzado(
        f"Fallo provocado por {current_user.nombre} para verificar el registro de errores."
    )
