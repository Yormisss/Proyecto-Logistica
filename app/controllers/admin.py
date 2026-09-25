"""Controlador del centro de distribucion.

Paso 1: tablero de control con los tres KPIs fundamentales del RF6.
Los modulos de pedidos, rutas e inventario se incorporan en los pasos siguientes.
"""

from flask import Blueprint, render_template
from flask_login import login_required
from sqlalchemy import func

from flask import request

from app.controllers.seguridad import requiere_rol
from app.extensions import db
from app.models import EstadoPedido, Pedido, Producto, Rol, Ruta, Usuario
from app.services import analitica
from app.tiempo import hoy

admin_bp = Blueprint("admin", __name__)


def calcular_kpis(fecha=None):
    """RF6 - Tablero de Control: KPIs fundamentales del prototipo.

    1. Total de entregas del dia
    2. Entregas pendientes
    3. Porcentaje de exito de entrega
    """
    fecha = fecha or hoy()

    conteos = dict(
        db.session.query(Pedido.estado, func.count(Pedido.id))
        .filter(Pedido.fecha_despacho == fecha)
        .group_by(Pedido.estado)
        .all()
    )

    total = sum(conteos.values())
    entregados = conteos.get(EstadoPedido.ENTREGADO, 0)
    fallidos = conteos.get(EstadoPedido.FALLIDO, 0)
    pendientes = sum(conteos.get(estado, 0) for estado in EstadoPedido.ABIERTOS)
    cerrados = entregados + fallidos

    return {
        "fecha": fecha,
        "total_dia": total,
        "entregados": entregados,
        "fallidos": fallidos,
        "pendientes": pendientes,
        "en_ruta": conteos.get(EstadoPedido.EN_RUTA, 0),
        "sin_asignar": conteos.get(EstadoPedido.PENDIENTE, 0),
        "porcentaje_exito": round(entregados * 100 / cerrados, 1) if cerrados else 0.0,
        "conteos": conteos,
    }


@admin_bp.route("/")
@login_required
@requiere_rol(Rol.ADMIN, Rol.DESPACHADOR)
def dashboard():
    kpis = calcular_kpis()

    rutas_hoy = (
        db.session.query(Ruta)
        .filter(Ruta.fecha == kpis["fecha"])
        .order_by(Ruta.codigo)
        .all()
    )
    productos_criticos = (
        db.session.query(Producto)
        .filter(Producto.activo.is_(True), Producto.stock_actual <= Producto.stock_minimo)
        .order_by(Producto.stock_actual)
        .limit(8)
        .all()
    )
    conductores_activos = (
        db.session.query(func.count(Usuario.id))
        .filter(Usuario.rol == Rol.CONDUCTOR, Usuario.activo.is_(True))
        .scalar()
    )

    return render_template(
        "admin/dashboard.html",
        kpis=kpis,
        rutas_hoy=rutas_hoy,
        productos_criticos=productos_criticos,
        conductores_activos=conductores_activos,
    )


@admin_bp.route("/analitica")
@login_required
@requiere_rol(Rol.ADMIN, Rol.DESPACHADOR)
def analitica_operacion():
    """Indicadores de apoyo a la toma de decisiones estrategicas."""
    dias = request.args.get("dias", 14, type=int)
    dias = dias if dias in (7, 14, 30) else 14

    serie = analitica.serie_entregas(dias)
    maximo_dia = max((punto["total"] for punto in serie), default=0)

    motivos = analitica.motivos_fallo(dias)
    conductores = analitica.productividad_conductores(dias)

    return render_template(
        "admin/analitica.html",
        dias=dias,
        serie=serie,
        maximo_dia=maximo_dia,
        motivos=motivos,
        maximo_motivo=max((m["cantidad"] for m in motivos), default=0),
        conductores=conductores,
        maximo_conductor=max((c["cerradas"] for c in conductores), default=0),
        tiempo=analitica.tiempo_promedio_entrega(dias),
        ventana=analitica.cumplimiento_ventana(dias),
    )


@admin_bp.route("/rendimiento")
@login_required
@requiere_rol(Rol.ADMIN)
def rendimiento():
    """RNF2 - Evidencia medida de los tiempos de respuesta del sistema."""
    horas = request.args.get("horas", 24, type=int)
    horas = horas if horas in (1, 24, 168) else 24

    datos = analitica.rendimiento(horas)
    maximo = max((t["p95_ms"] for t in datos["transacciones"]), default=0)
    escala = max(maximo, datos["umbral_maximo_ms"]) * 1.1

    return render_template(
        "admin/rendimiento.html", datos=datos, escala_ms=escala or 1,
    )
