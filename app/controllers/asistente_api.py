"""Custom functions de Retell para el asistente de voz del conductor.

Retell llama a estos endpoints desde sus servidores durante la conversacion,
sin cookie de sesion ni token CSRF (el blueprint esta exento). Por eso cada
peticion se autentica en dos pasos antes de llegar a la vista:

  1. La firma X-Retell-Signature sobre el cuerpo crudo prueba que la peticion
     viene de Retell (401 si falta o no es valida).
  2. El `call.call_id` del cuerpo identifica al conductor que abrio la
     llamada en `sesiones_asistente` (403 si no existe, vencio o no es de un
     conductor).

Las vistas solo operan sobre la ruta de hoy de ese conductor y aplican las
transiciones con `app.services.despacho`, igual que la vista movil. No hay
funcion para confirmar entregas: esa requiere la prueba de entrega en pantalla.

Las respuestas son texto breve en espanol, pensado para leerse en voz alta.
Los errores de negocio (transicion invalida, parada inexistente) responden 200
con el mensaje para que el agente se lo diga al conductor.
"""

from flask import Blueprint, g, jsonify, request

from app.extensions import db
from app.models import EstadoPedido, Rol
from app.services.asistente import firma_valida, sesion_vigente
from app.services.despacho import TransicionInvalida, cambiar_estado, ruta_del_dia

asistente_api_bp = Blueprint("asistente_api", __name__)

NOTA_ASISTENTE = "Registrado por el asistente de voz"

# Mismo limite que PruebaEntrega.motivo_fallo.
LARGO_MAXIMO_MOTIVO = 160

PENDIENTES = (EstadoPedido.ASIGNADO, EstadoPedido.EN_RUTA)


@asistente_api_bp.before_request
def autenticar_llamada():
    cuerpo = request.get_data(as_text=True)
    if not firma_valida(cuerpo, request.headers.get("X-Retell-Signature")):
        return jsonify(error="Firma invalida."), 401

    datos = request.get_json(silent=True)
    if not isinstance(datos, dict):
        datos = {}
    llamada = datos.get("call") if isinstance(datos.get("call"), dict) else {}

    sesion = sesion_vigente(llamada.get("call_id"), Rol.CONDUCTOR)
    if sesion is None:
        return jsonify(error="Llamada no autorizada."), 403

    g.conductor = sesion.usuario
    g.argumentos = datos.get("args") if isinstance(datos.get("args"), dict) else {}


def _responder(mensaje):
    return jsonify(mensaje=mensaje)


def _cantidad(numero, singular, plural=None):
    """"1 parada", "3 paradas": el agente lee el texto tal cual."""
    return f"{numero} {singular if numero == 1 else (plural or singular + 's')}"


def _ventana(pedido):
    if pedido.ventana_inicio and pedido.ventana_fin:
        return (
            f"de {pedido.ventana_inicio.strftime('%H:%M')} "
            f"a {pedido.ventana_fin.strftime('%H:%M')}"
        )
    return "sin restricción de horario"


def _ruta():
    return ruta_del_dia(g.conductor.id)


def _parada(ruta):
    """Pedido de la ruta con el `orden` pedido, o (None, mensaje) si no hay."""
    try:
        orden = int(g.argumentos.get("orden"))
    except (TypeError, ValueError):
        return None, "Necesito el número de la parada."

    for pedido in ruta.pedidos:
        if pedido.orden_en_ruta == orden:
            return pedido, None
    return None, f"No encontré la parada {orden} en tu ruta de hoy."


SIN_RUTA = "No tienes una ruta activa para hoy."


@asistente_api_bp.route("/mi-ruta", methods=["POST"])
def mi_ruta():
    ruta = _ruta()
    if ruta is None:
        return _responder(SIN_RUTA)

    pedidos = ruta.pedidos

    def conteo(*estados):
        return sum(1 for p in pedidos if p.estado in estados)

    partes = [
        f"Tu ruta de hoy es la {ruta.codigo}, con {_cantidad(len(pedidos), 'parada')}:",
        f"{_cantidad(conteo(EstadoPedido.ENTREGADO), 'entregada')},",
        f"{_cantidad(conteo(EstadoPedido.FALLIDO), 'fallida')} y",
        f"{_cantidad(conteo(*PENDIENTES), 'pendiente')}.",
    ]
    siguiente = next((p for p in pedidos if p.estado in PENDIENTES), None)
    if siguiente is not None:
        partes.append(
            f"La siguiente es la parada {siguiente.orden_en_ruta}, {siguiente.cliente_nombre}."
        )
    return _responder(" ".join(partes))


@asistente_api_bp.route("/siguiente-parada", methods=["POST"])
def siguiente_parada():
    ruta = _ruta()
    if ruta is None:
        return _responder(SIN_RUTA)

    pedido = next((p for p in ruta.pedidos if p.estado in PENDIENTES), None)
    if pedido is None:
        return _responder("No te quedan paradas pendientes en la ruta de hoy.")

    return _responder(
        f"La siguiente es la parada {pedido.orden_en_ruta}: {pedido.cliente_nombre}, "
        f"en {pedido.direccion}. Ventana {_ventana(pedido)}. "
        f"Estado: {pedido.estado_etiqueta.lower()}."
    )


@asistente_api_bp.route("/detalle-parada", methods=["POST"])
def detalle_parada():
    ruta = _ruta()
    if ruta is None:
        return _responder(SIN_RUTA)
    pedido, error = _parada(ruta)
    if pedido is None:
        return _responder(error)

    unidades = sum(item.cantidad for item in pedido.items)
    partes = [
        f"Parada {pedido.orden_en_ruta}: {pedido.cliente_nombre}, en {pedido.direccion}"
        + (f", {pedido.ciudad}." if pedido.ciudad else "."),
        f"Ventana {_ventana(pedido)}.",
        f"Estado: {pedido.estado_etiqueta.lower()}.",
        f"Lleva {_cantidad(unidades, 'unidad', 'unidades')}.",
    ]
    if pedido.cliente_telefono:
        partes.append(f"Teléfono del cliente: {pedido.cliente_telefono}.")
    if pedido.observaciones:
        partes.append(f"Observaciones: {pedido.observaciones}")
    return _responder(" ".join(partes))


@asistente_api_bp.route("/marcar-en-camino", methods=["POST"])
def marcar_en_camino():
    ruta = _ruta()
    if ruta is None:
        return _responder(SIN_RUTA)
    pedido, error = _parada(ruta)
    if pedido is None:
        return _responder(error)

    try:
        cambiar_estado(pedido, EstadoPedido.EN_RUTA, g.conductor.id, nota=NOTA_ASISTENTE)
    except TransicionInvalida as motivo:
        db.session.rollback()
        return _responder(f"No pude marcar la parada {pedido.orden_en_ruta} en camino. {motivo}")

    db.session.commit()
    return _responder(
        f"Listo, la parada {pedido.orden_en_ruta}, {pedido.cliente_nombre}, quedó en camino."
    )


@asistente_api_bp.route("/registrar-fallo", methods=["POST"])
def registrar_fallo():
    ruta = _ruta()
    if ruta is None:
        return _responder(SIN_RUTA)
    pedido, error = _parada(ruta)
    if pedido is None:
        return _responder(error)

    motivo = str(g.argumentos.get("motivo") or "").strip()[:LARGO_MAXIMO_MOTIVO]
    if not motivo:
        return _responder("Necesito el motivo por el que no se pudo entregar.")

    try:
        cambiar_estado(
            pedido,
            EstadoPedido.FALLIDO,
            g.conductor.id,
            nota=NOTA_ASISTENTE,
            motivo_fallo=motivo,
        )
    except TransicionInvalida as causa:
        db.session.rollback()
        return _responder(
            f"No pude registrar el fallo de la parada {pedido.orden_en_ruta}. {causa}"
        )

    db.session.commit()
    return _responder(
        f"Registré la parada {pedido.orden_en_ruta}, {pedido.cliente_nombre}, "
        f"como no entregada por: {motivo}. El inventario no se afectó."
    )
