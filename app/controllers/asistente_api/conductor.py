"""Funciones del asistente de voz del conductor.

Solo operan sobre la ruta de hoy del conductor de la llamada y aplican las
transiciones con `app.services.despacho`, igual que la vista movil. No hay
funcion para confirmar entregas: esa requiere la prueba de entrega en pantalla.
"""

from flask import g

from app.controllers.asistente_api import (
    NOTA_ASISTENTE, cantidad, funcion_asistente, responder,
)
from app.extensions import db
from app.models import EstadoPedido, Rol
from app.services.despacho import (
    TransicionInvalida, cambiar_estado, ruta_del_dia, ruta_finalizada_del_dia,
)

ESPACIO = "conductor"
ROLES = (Rol.CONDUCTOR,)

# Mismo limite que PruebaEntrega.motivo_fallo.
LARGO_MAXIMO_MOTIVO = 160

PENDIENTES = (EstadoPedido.ASIGNADO, EstadoPedido.EN_RUTA)

SIN_RUTA = "No tienes una ruta activa para hoy."

PARAMETRO_ORDEN = {
    "orden": {"type": "integer", "description": "Numero de la parada en la ruta de hoy."},
}


def _ventana(pedido):
    if pedido.ventana_inicio and pedido.ventana_fin:
        return (
            f"de {pedido.ventana_inicio.strftime('%H:%M')} "
            f"a {pedido.ventana_fin.strftime('%H:%M')}"
        )
    return "sin restricción de horario"


def _ruta():
    return ruta_del_dia(g.usuario.id)


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


@funcion_asistente(
    ESPACIO, "mi-ruta", roles=ROLES,
    descripcion="Resume la ruta de hoy del conductor: paradas entregadas, fallidas, "
                "pendientes y la siguiente. Si ya la termino, el resumen del cierre.",
)
def mi_ruta():
    ruta = _ruta()
    if ruta is None:
        finalizada = ruta_finalizada_del_dia(g.usuario.id)
        if finalizada is None:
            return responder(SIN_RUTA)
        return responder(
            f"Completaste tu ruta de hoy, la {finalizada.codigo}, con "
            f"{cantidad(finalizada.total_paradas, 'parada')}: "
            f"{cantidad(finalizada.contar(EstadoPedido.ENTREGADO), 'entregada')}, "
            f"{cantidad(finalizada.contar(EstadoPedido.FALLIDO), 'fallida')} y "
            f"{cantidad(finalizada.contar(EstadoPedido.CANCELADO), 'cancelada')}. "
            "Puedes ver el detalle en tu historial."
        )

    pedidos = ruta.pedidos
    partes = [
        f"Tu ruta de hoy es la {ruta.codigo}, con {cantidad(len(pedidos), 'parada')}:",
        f"{cantidad(ruta.contar(EstadoPedido.ENTREGADO), 'entregada')},",
        f"{cantidad(ruta.contar(EstadoPedido.FALLIDO), 'fallida')} y",
        f"{cantidad(ruta.contar(*PENDIENTES), 'pendiente')}.",
    ]
    siguiente = next((p for p in pedidos if p.estado in PENDIENTES), None)
    if siguiente is not None:
        partes.append(
            f"La siguiente es la parada {siguiente.orden_en_ruta}, {siguiente.cliente_nombre}."
        )
    return responder(" ".join(partes))


@funcion_asistente(
    ESPACIO, "siguiente-parada", roles=ROLES,
    descripcion="Primera parada pendiente de la ruta de hoy: cliente, direccion y ventana horaria.",
)
def siguiente_parada():
    ruta = _ruta()
    if ruta is None:
        return responder(SIN_RUTA)

    pedido = next((p for p in ruta.pedidos if p.estado in PENDIENTES), None)
    if pedido is None:
        return responder("No te quedan paradas pendientes en la ruta de hoy.")

    return responder(
        f"La siguiente es la parada {pedido.orden_en_ruta}: {pedido.cliente_nombre}, "
        f"en {pedido.direccion}. Ventana {_ventana(pedido)}. "
        f"Estado: {pedido.estado_etiqueta.lower()}."
    )


@funcion_asistente(
    ESPACIO, "detalle-parada", roles=ROLES,
    descripcion="Detalle de una parada de la ruta de hoy: cliente, direccion, ventana, estado, "
                "unidades, telefono y observaciones.",
    parametros=PARAMETRO_ORDEN, requeridos=("orden",),
)
def detalle_parada():
    ruta = _ruta()
    if ruta is None:
        return responder(SIN_RUTA)
    pedido, error = _parada(ruta)
    if pedido is None:
        return responder(error)

    unidades = sum(item.cantidad for item in pedido.items)
    partes = [
        f"Parada {pedido.orden_en_ruta}: {pedido.cliente_nombre}, en {pedido.direccion}"
        + (f", {pedido.ciudad}." if pedido.ciudad else "."),
        f"Ventana {_ventana(pedido)}.",
        f"Estado: {pedido.estado_etiqueta.lower()}.",
        f"Lleva {cantidad(unidades, 'unidad', 'unidades')}.",
    ]
    if pedido.cliente_telefono:
        partes.append(f"Teléfono del cliente: {pedido.cliente_telefono}.")
    if pedido.observaciones:
        partes.append(f"Observaciones: {pedido.observaciones}")
    return responder(" ".join(partes))


@funcion_asistente(
    ESPACIO, "marcar-en-camino", roles=ROLES, accion=True,
    descripcion="Pasa una parada de la ruta de hoy a EN_RUTA (tambien sirve para reintentar "
                "una fallida).",
    parametros=PARAMETRO_ORDEN, requeridos=("orden",),
)
def marcar_en_camino():
    ruta = _ruta()
    if ruta is None:
        return responder(SIN_RUTA)
    pedido, error = _parada(ruta)
    if pedido is None:
        return responder(error)

    try:
        cambiar_estado(pedido, EstadoPedido.EN_RUTA, g.usuario.id, nota=NOTA_ASISTENTE)
    except TransicionInvalida as motivo:
        db.session.rollback()
        return responder(f"No pude marcar la parada {pedido.orden_en_ruta} en camino. {motivo}")

    db.session.commit()
    return responder(
        f"Listo, la parada {pedido.orden_en_ruta}, {pedido.cliente_nombre}, quedó en camino."
    )


@funcion_asistente(
    ESPACIO, "registrar-fallo", roles=ROLES, accion=True,
    descripcion="Pasa una parada de la ruta de hoy a FALLIDO con el motivo; no toca el inventario.",
    parametros={
        **PARAMETRO_ORDEN,
        "motivo": {"type": "string", "description": "Por que no se pudo entregar."},
    },
    requeridos=("orden", "motivo"),
)
def registrar_fallo():
    ruta = _ruta()
    if ruta is None:
        return responder(SIN_RUTA)
    pedido, error = _parada(ruta)
    if pedido is None:
        return responder(error)

    motivo = str(g.argumentos.get("motivo") or "").strip()[:LARGO_MAXIMO_MOTIVO]
    if not motivo:
        return responder("Necesito el motivo por el que no se pudo entregar.")

    try:
        cambiar_estado(
            pedido,
            EstadoPedido.FALLIDO,
            g.usuario.id,
            nota=NOTA_ASISTENTE,
            motivo_fallo=motivo,
        )
    except TransicionInvalida as causa:
        db.session.rollback()
        return responder(
            f"No pude registrar el fallo de la parada {pedido.orden_en_ruta}. {causa}"
        )

    db.session.commit()
    return responder(
        f"Registré la parada {pedido.orden_en_ruta}, {pedido.cliente_nombre}, "
        f"como no entregada por: {motivo}. El inventario no se afectó."
    )
