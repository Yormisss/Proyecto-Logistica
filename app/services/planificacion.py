"""Cambios a rutas ya creadas (RF3): recalcular la secuencia y sumar paradas.

Lo usan la pantalla de la ruta ("Recalcular") y el asistente de voz del
gestor, para que ambos reordenen las paradas con la misma regla: las cerradas
conservan su posicion y solo se reordena lo pendiente.
"""

from flask import current_app

from app.models import EstadoPedido, EstadoRuta, EventoPedido
from app.extensions import db
from app.services.ruteo import ESTRATEGIA_DISTANCIA, calcular_ruta


class PlanificacionInvalida(Exception):
    """El cambio pedido a la ruta no esta permitido."""


def origen_centro_distribucion():
    return (current_app.config["CD_LAT"], current_app.config["CD_LNG"])


def recalcular_secuencia(ruta, estrategia=ESTRATEGIA_DISTANCIA):
    """Reordena las paradas abiertas de la ruta. No hace commit.

    Devuelve el ResultadoRuteo, o None si no quedan paradas abiertas. Lanza
    PlanificacionInvalida si la ruta ya finalizo.
    """
    if ruta.estado == EstadoRuta.FINALIZADA:
        raise PlanificacionInvalida("No se puede recalcular una ruta finalizada.")

    abiertos = [p for p in ruta.pedidos if p.estado in EstadoPedido.ABIERTOS]
    if not abiertos:
        return None

    resultado = calcular_ruta(origen_centro_distribucion(), abiertos, estrategia)

    cerrados = [p for p in ruta.pedidos if p.estado in EstadoPedido.FINALES]
    posiciones = {pedido_id: indice for indice, pedido_id in enumerate(resultado.orden, start=1)}
    desplazamiento = len(cerrados)

    for indice, pedido in enumerate(cerrados, start=1):
        pedido.orden_en_ruta = indice
    for pedido in abiertos:
        pedido.orden_en_ruta = posiciones.get(pedido.id, 0) + desplazamiento

    ruta.distancia_km = resultado.distancia_km
    ruta.duracion_min = resultado.duracion_min
    ruta.geometria = resultado.geometria
    ruta.proveedor_ruteo = resultado.proveedor
    return resultado


def verificar_agregar_a_ruta(pedido, ruta):
    """Lanza PlanificacionInvalida si `pedido` no puede sumarse a `ruta`.

    Solo un pedido PENDIENTE y sin ruta, con la misma fecha de despacho que la
    ruta, y solo a una ruta abierta: una ruta finalizada no se reabre.
    """
    if ruta.estado == EstadoRuta.FINALIZADA:
        raise PlanificacionInvalida(
            f"La ruta {ruta.codigo} ya está finalizada y no se reabre. Para asignar más "
            "pedidos hoy a ese conductor, cree una ruta nueva en pantalla."
        )
    if ruta.estado == EstadoRuta.CANCELADA:
        raise PlanificacionInvalida(f"La ruta {ruta.codigo} está cancelada.")
    if pedido.estado != EstadoPedido.PENDIENTE or pedido.ruta_id is not None:
        raise PlanificacionInvalida(
            f"El pedido {pedido.codigo} está {pedido.estado_etiqueta.lower()}; solo se pueden "
            "agregar pedidos pendientes sin ruta."
        )
    if pedido.fecha_despacho != ruta.fecha:
        raise PlanificacionInvalida(
            f"El pedido {pedido.codigo} es para el {pedido.fecha_despacho.strftime('%d/%m/%Y')} "
            f"y la ruta {ruta.codigo} es del {ruta.fecha.strftime('%d/%m/%Y')}."
        )


def agregar_a_ruta(pedido, ruta, usuario_id, origen=None, estrategia=ESTRATEGIA_DISTANCIA):
    """Suma un pedido pendiente a una ruta abierta y recalcula la secuencia.

    El pedido pasa a ASIGNADO con su evento en la bitacora (`origen` se agrega
    a la nota, p. ej. "Registrado por el asistente de voz"). No hace commit.
    Devuelve el ResultadoRuteo del recalculo.
    """
    verificar_agregar_a_ruta(pedido, ruta)

    pedido.ruta = ruta
    pedido.estado = EstadoPedido.ASIGNADO
    nota = f"Asignado a la ruta {ruta.codigo}"
    db.session.add(
        EventoPedido(
            pedido_id=pedido.id,
            usuario_id=usuario_id,
            estado_anterior=EstadoPedido.PENDIENTE,
            estado_nuevo=EstadoPedido.ASIGNADO,
            nota=f"{nota}. {origen}" if origen else nota,
        )
    )
    return recalcular_secuencia(ruta, estrategia)
