"""Bitacora: consulta de la auditoria (solo el administrador).

Solo lectura: la auditoria no se edita ni se borra. Filtra por fecha,
usuario, origen y accion, del registro mas reciente al mas antiguo.
"""

from datetime import date, datetime, time, timedelta

from flask import Blueprint, render_template, request
from flask_login import login_required

from app.controllers.seguridad import requiere_rol
from app.extensions import db
from app.models import Auditoria, Rol, Usuario
from app.services.notificaciones import ORIGEN_AUTOMATICO, ORIGEN_PANTALLA, ORIGEN_VOZ

bitacora_bp = Blueprint("bitacora", __name__)

POR_PAGINA = 50
ORIGENES = (ORIGEN_VOZ, ORIGEN_PANTALLA, ORIGEN_AUTOMATICO)


def _fecha(texto):
    try:
        return date.fromisoformat((texto or "").strip())
    except ValueError:
        return None


@bitacora_bp.route("/")
@login_required
@requiere_rol(Rol.ADMIN)
def lista():
    consulta = db.session.query(Auditoria)

    desde = _fecha(request.args.get("desde"))
    hasta = _fecha(request.args.get("hasta"))
    if desde:
        consulta = consulta.filter(Auditoria.registrado_en >= datetime.combine(desde, time.min))
    if hasta:
        consulta = consulta.filter(
            Auditoria.registrado_en < datetime.combine(hasta + timedelta(days=1), time.min)
        )

    usuario_id = request.args.get("usuario", type=int)
    if usuario_id:
        consulta = consulta.filter(Auditoria.usuario_id == usuario_id)

    origen = request.args.get("origen", "").strip()
    if origen in ORIGENES:
        consulta = consulta.filter(Auditoria.origen == origen)
    else:
        origen = ""

    accion = request.args.get("accion", "").strip()
    if accion:
        consulta = consulta.filter(Auditoria.accion == accion)

    total = consulta.count()
    pagina = max(request.args.get("pagina", 1, type=int), 1)
    registros = (
        consulta.order_by(Auditoria.registrado_en.desc(), Auditoria.id.desc())
        .offset((pagina - 1) * POR_PAGINA)
        .limit(POR_PAGINA)
        .all()
    )

    usuarios = (
        db.session.query(Usuario)
        .filter(Usuario.id.in_(db.session.query(Auditoria.usuario_id).distinct()))
        .order_by(Usuario.nombre)
        .all()
    )
    acciones = [fila[0] for fila in
                db.session.query(Auditoria.accion).distinct().order_by(Auditoria.accion)]

    filtros = {"desde": desde.isoformat() if desde else "", "hasta": hasta.isoformat() if hasta else "",
               "usuario": usuario_id or "", "origen": origen, "accion": accion}
    return render_template(
        "admin/bitacora.html",
        registros=registros,
        total=total,
        pagina=pagina,
        paginas=max((total + POR_PAGINA - 1) // POR_PAGINA, 1),
        filtros=filtros,
        hay_filtros=any(filtros.values()),
        usuarios=usuarios,
        acciones=acciones,
        origenes=ORIGENES,
        etiquetas_rol=Rol.ETIQUETAS,
    )
