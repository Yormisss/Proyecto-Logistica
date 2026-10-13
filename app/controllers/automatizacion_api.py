"""Endpoints que consultan los escenarios programados de Make.

Make los llama desde sus servidores, sin cookie de sesion ni token CSRF (el
blueprint esta exento). Cada peticion se autentica con el encabezado
X-Automatizacion-Token, comparado en tiempo constante contra
AUTOMATIZACION_TOKEN:

  * sin AUTOMATIZACION_TOKEN configurado, el endpoint no existe (404);
  * con el token ausente o distinto, 401.

Las cifras salen de `app.services.analitica`, las mismas funciones del
tablero, para que el correo y la pantalla nunca muestren numeros distintos.

Como todo lo que Make envia, la respuesta lleva quien origino la accion; aqui
es el sistema ("Sistema SGDS (automático)", origen "automático"), tanto en el
resumen como en cada correo de reprogramacion.

`/auditoria` entrega la auditoria por lotes para que Make llene una hoja de
Google Sheets sin huecos ni filas repetidas (ver `auditoria`).
"""

import hmac
from datetime import timedelta

from flask import Blueprint, abort, current_app, jsonify, render_template, request
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import Auditoria, Cliente, EstadoPedido, Pedido
from app.services import analitica
from app.services.auditoria import COLUMNAS_HOJA, fila_hoja
from app.services.notificaciones import ORIGEN_AUTOMATICO, datos_actor
from app.tiempo import ahora, hoy, iso_bogota

# Solo se entregan registros con al menos esta antiguedad: para entonces la
# transaccion que los escribio ya termino (dura milisegundos), y lo entregado
# no cambia despues. Ver `auditoria`.
ANTIGUEDAD_MINIMA = timedelta(seconds=60)
LIMITE_POR_DEFECTO = 200
LIMITE_MAXIMO = 500

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
            **datos_actor(origen=ORIGEN_AUTOMATICO),
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
        fecha_hora=iso_bogota(),
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
        **datos_actor(origen=ORIGEN_AUTOMATICO),
    )


@automatizacion_api_bp.route("/auditoria", methods=["GET"])
def auditoria():
    """Registros de auditoria posteriores a `desde_id`, por lotes, para Google Sheets.

    GET /api/automatizacion/auditoria?desde_id=<n>&limite=<m>

    Devuelve `columnas` (en el orden de la hoja), `filas` (una lista por
    registro, en ese orden, para "Bulk Add Rows"), `siguiente_desde_id` (el
    cursor para la proxima consulta) y `hay_mas`.

    Sin huecos: se recorre por `id` y se corta en el primer registro con menos
    de ANTIGUEDAD_MINIMA. En MySQL, dos transacciones simultaneas pueden
    confirmar el id 11 antes que el 10; cortar por antiguedad en orden de id
    (y no filtrar por fecha) evita entregar el 11 y saltarse el 10. Los ids
    que nunca existieron (un rollback) no importan: el cursor es "mayor que".
    Sin repetidos: Make guarda `siguiente_desde_id` solo si escribio el lote;
    si lo reintenta, `id_auditoria` identifica la fila ya escrita.
    """
    desde_id = request.args.get("desde_id", default=0, type=int)
    limite = request.args.get("limite", default=LIMITE_POR_DEFECTO, type=int)
    if desde_id is None or desde_id < 0:
        return jsonify(error="desde_id debe ser un entero mayor o igual a cero."), 400
    limite = max(1, min(limite or LIMITE_POR_DEFECTO, LIMITE_MAXIMO))

    corte = ahora() - ANTIGUEDAD_MINIMA
    candidatos = (
        db.session.query(Auditoria)
        .filter(Auditoria.id > desde_id)
        .order_by(Auditoria.id)
        .limit(limite + 1)
        .all()
    )
    listos = []
    for registro in candidatos:
        if registro.registrado_en > corte:
            break
        listos.append(registro)
    lote = listos[:limite]

    return jsonify(
        columnas=list(COLUMNAS_HOJA),
        filas=[fila_hoja(r) for r in lote],
        siguiente_desde_id=lote[-1].id if lote else desde_id,
        hay_mas=len(listos) > limite,
        fecha_hora=iso_bogota(),
    )
