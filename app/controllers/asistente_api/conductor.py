"""Funciones del asistente de voz del conductor.

Solo operan sobre la ruta de hoy del conductor de la llamada y aplican las
transiciones con `app.services.despacho`, igual que la vista movil. La entrega
por voz exige el nombre y el documento de quien recibe; la llamada no envia la
ubicacion del telefono, y la prueba de entrega lo dice en su observacion.

Las acciones son de rutina y de alto volumen: quedan en la auditoria sin el
correo accion_asistente (avisar=False); su historial esta en los eventos.
"""

from flask import g

from app.controllers.asistente_api import (
    NOTA_ASISTENTE, cantidad, exigir_confirmacion, funcion_agrupada, funcion_asistente, responder,
)
from app.controllers.asistente_api.comun import fecha_larga, me_falta, texto
from app.extensions import db
from app.models import EstadoPedido, Rol, Ruta
from app.services.configuracion import ConfiguracionInvalida, interpretar_motivo_voz
from app.services.despacho import (
    TransicionInvalida, cambiar_estado, iniciar_ruta, ruta_del_dia, ruta_finalizada_del_dia,
    validar_transicion,
)

ESPACIO = "conductor"
ROLES = (Rol.CONDUCTOR,)

PENDIENTES = (EstadoPedido.ASIGNADO, EstadoPedido.EN_RUTA)

SIN_RUTA = "No tienes una ruta activa para hoy."

# Observacion de la prueba de una entrega hecha por voz: la llamada no envia la
# ubicacion del telefono, como si lo hace la pantalla.
OBSERVACION_ENTREGA_VOZ = "Registrada por el asistente de voz, sin ubicación"
# Mismos largos que el formulario de entrega de la pantalla.
LARGO_RECEPTOR = 160
LARGO_DOCUMENTO = 40
# Rutas que lee mi-historial (la pantalla lista 30).
LIMITE_HISTORIAL = 5

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
    ESPACIO, "iniciar-ruta", roles=ROLES, accion=True, entidad="ruta", avisar=False,
    descripcion="Inicia la ruta de hoy: pasa a en camino todas las paradas asignadas, como el "
                "boton Iniciar ruta de la pantalla.",
)
def iniciar_ruta_voz():
    ruta = _ruta()
    if ruta is None:
        return responder(SIN_RUTA)
    asignadas = [p for p in ruta.pedidos if p.estado == EstadoPedido.ASIGNADO]
    if not asignadas:
        return responder(f"La ruta {ruta.codigo} no tiene paradas asignadas por iniciar.")

    pendiente = exigir_confirmacion(
        f"Voy a iniciar tu ruta {ruta.codigo}: "
        f"{cantidad(len(asignadas), 'parada pasa', 'paradas pasan')} a en camino."
    )
    if pendiente:
        return pendiente

    afectados, advertencias = iniciar_ruta(ruta, g.usuario.id)
    db.session.commit()
    if not afectados:
        return responder(" ".join(["No había paradas pendientes por iniciar.", *advertencias]))
    return responder(" ".join([
        f"Listo, la ruta {ruta.codigo} quedó iniciada con "
        f"{cantidad(len(afectados), 'parada')} en camino.",
        *advertencias,
    ]))


@funcion_asistente(
    ESPACIO, "mi-historial", roles=ROLES,
    descripcion="Ultimas rutas del conductor, de la mas reciente a la mas antigua: fecha, estado "
                "y cuantas paradas entrego y cuantas fallaron.",
)
def mi_historial():
    rutas = (
        db.session.query(Ruta)
        .filter(Ruta.conductor_id == g.usuario.id)
        .order_by(Ruta.fecha.desc(), Ruta.codigo.desc())
        .limit(LIMITE_HISTORIAL)
        .all()
    )
    if not rutas:
        return responder("Aún no tienes rutas registradas.")

    def ruta_texto(ruta):
        return (f"la {ruta.codigo} del {fecha_larga(ruta.fecha)}, {ruta.estado_etiqueta.lower()}, "
                f"con {cantidad(ruta.total_paradas, 'parada')}: "
                f"{cantidad(ruta.contar(EstadoPedido.ENTREGADO), 'entregada')} y "
                f"{cantidad(ruta.contar(EstadoPedido.FALLIDO), 'fallida')}")

    inicio = "Tu última ruta es " if len(rutas) == 1 else f"Tus últimas {len(rutas)} rutas: "
    return responder(inicio + "; ".join(ruta_texto(r) for r in rutas) + ".")


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


parada = funcion_agrupada(
    ESPACIO, "parada", roles=ROLES, entidad="pedido",
    descripcion="Acciones sobre una parada de la ruta de hoy, por su numero.",
    parametros={
        **PARAMETRO_ORDEN,
        "motivo": {"type": "string", "description": "fallo: por que no se pudo entregar."},
        "recibe": {"type": "string", "description": "entregar: nombre de quien recibe."},
        "documento": {"type": "string", "description": "entregar: documento de quien recibe."},
    },
)


@parada("en-camino", "Marcar parada en camino",
        "pasa la parada a EN_RUTA; tambien sirve para reintentar una fallida; requiere orden",
        avisar=False)
def marcar_en_camino():
    ruta = _ruta()
    if ruta is None:
        return responder(SIN_RUTA)
    pedido, error = _parada(ruta)
    if pedido is None:
        return responder(error)

    try:
        validar_transicion(pedido.estado, EstadoPedido.EN_RUTA)
    except TransicionInvalida as motivo:
        return responder(f"No pude marcar la parada {pedido.orden_en_ruta} en camino. {motivo}")

    pendiente = exigir_confirmacion(
        f"Voy a marcar en camino la parada {pedido.orden_en_ruta}, {pedido.cliente_nombre}."
    )
    if pendiente:
        return pendiente

    try:
        cambiar_estado(pedido, EstadoPedido.EN_RUTA, g.usuario.id, nota=NOTA_ASISTENTE)
    except TransicionInvalida as motivo:
        db.session.rollback()
        return responder(f"No pude marcar la parada {pedido.orden_en_ruta} en camino. {motivo}")

    db.session.commit()
    return responder(
        f"Listo, la parada {pedido.orden_en_ruta}, {pedido.cliente_nombre}, quedó en camino."
    )


@parada("fallo", "Registrar entrega fallida",
        "pasa la parada a FALLIDO con el motivo, sin tocar el inventario; el motivo se compara "
        "con el catalogo y, si no coincide, queda como 'Otro' con lo dicho; requiere orden y "
        "motivo", avisar=False)
def registrar_fallo():
    ruta = _ruta()
    if ruta is None:
        return responder(SIN_RUTA)
    pedido, error = _parada(ruta)
    if pedido is None:
        return responder(error)

    # Lo dicho se compara con el catalogo; si no coincide con ningun motivo, se
    # registra como "Otro" con el texto dicho, y el resumen lo lee asi.
    try:
        motivo_catalogo, motivo = interpretar_motivo_voz(g.argumentos.get("motivo"))
    except ConfiguracionInvalida as error:
        return responder(str(error))
    como = (f"por otro motivo: {motivo}" if motivo_catalogo.es_otro
            else f"por: {motivo}")

    try:
        validar_transicion(pedido.estado, EstadoPedido.FALLIDO)
    except TransicionInvalida as causa:
        return responder(
            f"No pude registrar el fallo de la parada {pedido.orden_en_ruta}. {causa}"
        )

    pendiente = exigir_confirmacion(
        f"Voy a registrar la parada {pedido.orden_en_ruta}, {pedido.cliente_nombre}, "
        f"como no entregada {como}."
    )
    if pendiente:
        return pendiente

    try:
        cambiar_estado(
            pedido,
            EstadoPedido.FALLIDO,
            g.usuario.id,
            nota=NOTA_ASISTENTE,
            motivo_fallo=motivo,
            motivo_id=motivo_catalogo.id,
        )
    except TransicionInvalida as causa:
        db.session.rollback()
        return responder(
            f"No pude registrar el fallo de la parada {pedido.orden_en_ruta}. {causa}"
        )

    db.session.commit()
    return responder(
        f"Registré la parada {pedido.orden_en_ruta}, {pedido.cliente_nombre}, "
        f"como no entregada {como}. El inventario no se afectó."
    )


@parada("entregar", "Confirmar entrega",
        "confirma la entrega de la parada y descuenta el inventario, como en pantalla; requiere "
        "orden, el nombre (recibe) y el documento de quien recibe", avisar=False)
def confirmar_entrega():
    ruta = _ruta()
    if ruta is None:
        return responder(SIN_RUTA)
    pedido, error = _parada(ruta)
    if pedido is None:
        return responder(error)

    recibe, documento = texto("recibe"), texto("documento")
    faltan = [etiqueta for valor, etiqueta in ((recibe, "el nombre de quien recibe"),
                                                (documento, "su documento")) if not valor]
    if faltan:
        return responder(me_falta(faltan))
    if len(recibe) > LARGO_RECEPTOR or len(documento) > LARGO_DOCUMENTO:
        return responder(f"El nombre admite hasta {LARGO_RECEPTOR} caracteres y el documento "
                         f"hasta {LARGO_DOCUMENTO}.")

    try:
        validar_transicion(pedido.estado, EstadoPedido.ENTREGADO)
    except TransicionInvalida as motivo:
        return responder(f"No pude confirmar la entrega de la parada {pedido.orden_en_ruta}. {motivo}")

    unidades = sum(item.cantidad for item in pedido.items)
    pendiente = exigir_confirmacion(
        f"Voy a confirmar la entrega de la parada {pedido.orden_en_ruta}, {pedido.cliente_nombre}, "
        f"recibida por {recibe}, documento {documento}. Se descuentan "
        f"{cantidad(unidades, 'unidad', 'unidades')} del inventario."
    )
    if pendiente:
        return pendiente

    try:
        resultado = cambiar_estado(
            pedido,
            EstadoPedido.ENTREGADO,
            g.usuario.id,
            nota=NOTA_ASISTENTE,
            receptor_nombre=recibe,
            receptor_documento=documento,
            observacion=OBSERVACION_ENTREGA_VOZ,
        )
    except TransicionInvalida as motivo:
        db.session.rollback()
        return responder(f"No pude confirmar la entrega de la parada {pedido.orden_en_ruta}. {motivo}")

    db.session.commit()
    descontadas = sum(m.cantidad for m in resultado.movimientos)
    partes = [f"Listo, registré la entrega de la parada {pedido.orden_en_ruta}, "
              f"{pedido.cliente_nombre}, recibida por {recibe}."]
    if descontadas:
        partes.append(f"Se descontaron {cantidad(descontadas, 'unidad', 'unidades')} del inventario.")
    return responder(" ".join(partes + list(resultado.advertencias)))
