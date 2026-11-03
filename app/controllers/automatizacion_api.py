"""Endpoints que consultan los escenarios programados de Make.

Make los llama desde sus servidores, sin cookie de sesion ni token CSRF (el
blueprint esta exento). Cada peticion se autentica con el encabezado
X-Automatizacion-Token, comparado en tiempo constante contra
AUTOMATIZACION_TOKEN:

  * sin AUTOMATIZACION_TOKEN configurado, el endpoint no existe (404);
  * con el token ausente o distinto, 401.

Las cifras salen de `app.services.analitica`, las mismas funciones del
tablero, para que el correo y la pantalla nunca muestren numeros distintos.

`/resumen-diario` queda como respaldo: la app ahora ENVIA el resumen a Make
(evento `resumen_diario`, ver app/services/resumen_diario.py), con el mismo
contenido que arma `resumen_diario.contenido` para esta respuesta.

Como todo lo que Make envia, la respuesta lleva quien origino la accion; aqui
es el sistema ("Sistema SGDS (automático)", origen "automático"), tanto en el
resumen como en cada correo de reprogramacion.

`/auditoria` entrega la auditoria por lotes para que Make llene una hoja de
Google Sheets sin huecos ni filas repetidas (ver `auditoria`).
"""

import hmac
from datetime import timedelta

from flask import Blueprint, abort, current_app, jsonify, request

from app.extensions import db
from app.models import Auditoria
from app.services import resumen_diario as servicio_resumen
from app.services.auditoria import COLUMNAS_HOJA, fila_hoja
from app.tiempo import ahora, iso_bogota

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


@automatizacion_api_bp.route("/resumen-diario", methods=["GET"])
def resumen_diario():
    return jsonify(servicio_resumen.contenido())


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
