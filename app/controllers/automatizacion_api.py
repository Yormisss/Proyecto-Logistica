"""Endpoints que consultan los escenarios programados de Make.

Make los llama desde sus servidores, sin cookie de sesion ni token CSRF (el
blueprint esta exento). Cada peticion se autentica con el encabezado
X-Automatizacion-Token, comparado en tiempo constante contra
AUTOMATIZACION_TOKEN:

  * sin AUTOMATIZACION_TOKEN configurado, el endpoint no existe (404);
  * con el token ausente o distinto, 401.

Las cifras salen de `app.services.analitica`, las mismas funciones del
tablero, para que el correo y la pantalla nunca muestren numeros distintos.
"""

import hmac

from flask import Blueprint, abort, current_app, jsonify, render_template, request
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import Cliente, EstadoPedido, Pedido
from app.services import analitica
from app.tiempo import hoy

automatizacion_api_bp = Blueprint("automatizacion_api", __name__)


@automatizacion_api_bp.before_request
def autenticar():
    esperado = current_app.config.get("AUTOMATIZACION_TOKEN") or ""
    if not esperado:
        abort(404)
    recibido = request.headers.get("X-Automatizacion-Token") or ""
    # Se comparan bytes: compare_digest rechaza str con caracteres no ASCII.
    if not hmac.compare_digest(recibido.encode("utf-8"), esperado.encode("utf-8")):
        return jsonify(error="Token invalido."), 401


def _fallidos_para_reprogramar(fecha):
    """Pedidos FALLIDOS del dia cuyo cliente tiene correo para coordinar otra visita."""
    pedidos = (
        db.session.query(Pedido)
        .join(Cliente, Pedido.cliente_id == Cliente.id)
        .options(joinedload(Pedido.prueba_entrega), joinedload(Pedido.cliente))
        .filter(
            Pedido.estado == EstadoPedido.FALLIDO,
            Pedido.fecha_despacho == fecha,
            Cliente.correo.isnot(None),
        )
        .order_by(Pedido.codigo)
        .all()
    )
    filas = []
    for pedido in pedidos:
        correo = (pedido.cliente.correo or "").strip()
        if not correo:
            continue
        filas.append({
            "codigo": pedido.codigo,
            "cliente": pedido.cliente_nombre,
            "correo": correo,
            "direccion": pedido.direccion_completa,
            "motivo": pedido.prueba_entrega.motivo_fallo if pedido.prueba_entrega else None,
        })
    return filas


@automatizacion_api_bp.route("/resumen-diario", methods=["GET"])
def resumen_diario():
    fecha = hoy()
    kpis_dia = analitica.kpis_del_dia(fecha)
    ventana = analitica.cumplimiento_ventana(dias=1, hasta=fecha)

    kpis = {
        "total": kpis_dia["total_dia"],
        "entregados": kpis_dia["entregados"],
        "fallidos": kpis_dia["fallidos"],
        "cancelados": kpis_dia["cancelados"],
        "pendientes": kpis_dia["pendientes"],
        "tasa_exito": kpis_dia["porcentaje_exito"],
        # None si hoy no hubo entregas con ventana horaria que medir.
        "cumplimiento_ventana": ventana["porcentaje"],
    }
    productos = [
        {
            "sku": producto.sku,
            "producto": producto.nombre,
            "stock_actual": producto.stock_actual,
            "stock_minimo": producto.stock_minimo,
            "negativo": producto.stock_actual < 0,
        }
        for producto in analitica.productos_bajo_minimo()
    ]
    fallidos = _fallidos_para_reprogramar(fecha)

    return jsonify(
        fecha=fecha.isoformat(),
        correo_destino=(current_app.config.get("CORREO_OPERACIONES") or "").strip() or None,
        kpis=kpis,
        productos_bajo_minimo=productos,
        fallidos_para_reprogramar=fallidos,
        resumen_html=render_template(
            "automatizacion/resumen_diario.html",
            fecha=fecha,
            kpis=kpis,
            productos=productos,
            fallidos=fallidos,
        ),
    )
