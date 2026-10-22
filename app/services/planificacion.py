"""Cambios a rutas ya creadas (RF3): recalcular la secuencia y sumar paradas.

Lo usan la pantalla de la ruta ("Recalcular") y el asistente de voz del
gestor, para que ambos reordenen las paradas con la misma regla: las cerradas
conservan su posicion y solo se reordena lo pendiente.
"""

from app.models import EstadoPedido, EstadoRuta, EventoPedido
from app.extensions import db
from app.services.ruteo import ESTRATEGIA_DISTANCIA, calcular_ruta


class PlanificacionInvalida(Exception):
    """El cambio pedido a la ruta no esta permitido."""


def origen_centro_distribucion():
    """(latitud, longitud) del centro de distribucion de la configuracion del negocio."""
    from app.services.configuracion import centro_distribucion

    centro = centro_distribucion()
    return (centro["latitud"], centro["longitud"])


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


# --------------------------------------------------------------------------
# Crear rutas, quitar paradas y eliminar rutas (pantalla y asistente de voz)
# --------------------------------------------------------------------------

def _nota(texto, origen):
    return f"{texto}. {origen}" if origen else texto


def _fecha(valor):
    from datetime import date

    if isinstance(valor, date):
        return valor
    try:
        return date.fromisoformat(str(valor).strip())
    except (TypeError, ValueError):
        return None


def validar_ruta(datos):
    """Datos de una ruta nueva; lanza DatosInvalidos si no cumplen.

    `datos`: fecha, conductor_id (conductor activo), vehiculo_id (opcional,
    vehiculo activo), estrategia y pedidos (ids de pedidos PENDIENTE, sin ruta
    y con esa fecha de despacho; al menos uno).
    """
    from app.models import Pedido, Rol, Usuario, Vehiculo
    from app.services.ruteo import ESTRATEGIAS
    from app.services.validacion import Validador

    v = Validador()
    fecha = None
    if not datos.get("fecha"):
        v.falta("fecha", "la fecha")
    else:
        fecha = _fecha(datos.get("fecha"))
        if fecha is None:
            v.invalido("fecha", "La fecha debe tener el formato año-mes-día.")

    conductor = None
    if not datos.get("conductor_id"):
        v.falta("conductor_id", "el conductor")
    else:
        conductor = db.session.get(Usuario, int(datos["conductor_id"]))
        if conductor is None or conductor.rol != Rol.CONDUCTOR or not conductor.activo:
            v.invalido("conductor_id", "El conductor no existe o no esta activo.")
            conductor = None

    vehiculo = None
    if datos.get("vehiculo_id"):
        vehiculo = db.session.get(Vehiculo, int(datos["vehiculo_id"]))
        if vehiculo is None or not vehiculo.activo:
            v.invalido("vehiculo_id", "El vehiculo no existe o no esta activo.")
            vehiculo = None

    estrategia = datos.get("estrategia") or ESTRATEGIA_DISTANCIA
    if estrategia not in ESTRATEGIAS:
        v.invalido("estrategia", "El criterio de ordenamiento no es valido.")

    ids = [int(x) for x in (datos.get("pedidos") or [])]
    pedidos = []
    if not ids:
        v.invalido("pedidos", "Seleccione al menos un pedido para la ruta.")
    elif fecha is not None:
        pedidos = (
            db.session.query(Pedido)
            .filter(Pedido.id.in_(ids), Pedido.ruta_id.is_(None),
                    Pedido.estado == EstadoPedido.PENDIENTE, Pedido.fecha_despacho == fecha)
            .all()
        )
        if len(pedidos) != len(set(ids)):
            v.invalido("pedidos", "Algunos pedidos ya fueron asignados a otra ruta o no son de esa "
                                  "fecha. Verifique la seleccion.")
    v.lanzar_si_hay()
    return {"fecha": fecha, "conductor": conductor, "vehiculo": vehiculo, "estrategia": estrategia,
            "pedidos": pedidos}


def crear_ruta(datos, usuario_id, origen=None):
    """Crea la ruta con sus paradas y calcula la secuencia. No hace commit.

    Devuelve (ruta, resultado del ruteo).
    """
    from app.models import Ruta
    from app.services.codigos import generar_codigo_ruta

    limpios = validar_ruta(datos)
    ruta = Ruta(
        codigo=generar_codigo_ruta(limpios["fecha"]),
        fecha=limpios["fecha"],
        estado=EstadoRuta.PLANIFICADA,
        conductor_id=limpios["conductor"].id,
        vehiculo_id=limpios["vehiculo"].id if limpios["vehiculo"] else None,
    )
    db.session.add(ruta)
    db.session.flush()
    for pedido in limpios["pedidos"]:
        pedido.ruta_id = ruta.id
        pedido.estado = EstadoPedido.ASIGNADO
        db.session.add(EventoPedido(
            pedido_id=pedido.id, usuario_id=usuario_id,
            estado_anterior=EstadoPedido.PENDIENTE, estado_nuevo=EstadoPedido.ASIGNADO,
            nota=_nota(f"Asignado a la ruta {ruta.codigo}", origen),
        ))
    resultado = calcular_ruta(origen_centro_distribucion(), limpios["pedidos"], limpios["estrategia"])
    posiciones = {pedido_id: indice for indice, pedido_id in enumerate(resultado.orden, start=1)}
    for pedido in limpios["pedidos"]:
        pedido.orden_en_ruta = posiciones.get(pedido.id)
    ruta.distancia_km = resultado.distancia_km
    ruta.duracion_min = resultado.duracion_min
    ruta.geometria = resultado.geometria
    ruta.proveedor_ruteo = resultado.proveedor
    return ruta, resultado


def verificar_quitar_parada(ruta, pedido):
    if pedido.ruta_id != ruta.id:
        raise PlanificacionInvalida(f"El pedido {pedido.codigo} no esta en la ruta {ruta.codigo}.")
    if pedido.estado in EstadoPedido.FINALES:
        raise PlanificacionInvalida("No se puede retirar una parada ya cerrada.")


def quitar_parada(ruta, pedido, usuario_id, origen=None):
    """Devuelve el pedido a la bolsa de pendientes, con su evento. No hace commit."""
    verificar_quitar_parada(ruta, pedido)
    anterior = pedido.estado
    pedido.ruta_id = None
    pedido.orden_en_ruta = None
    pedido.estado = EstadoPedido.PENDIENTE
    db.session.add(EventoPedido(
        pedido_id=pedido.id, usuario_id=usuario_id,
        estado_anterior=anterior, estado_nuevo=EstadoPedido.PENDIENTE,
        nota=_nota(f"Retirado de la ruta {ruta.codigo}", origen),
    ))


def verificar_eliminar_ruta(ruta):
    if any(p.estado in EstadoPedido.FINALES for p in ruta.pedidos):
        raise PlanificacionInvalida("No se puede eliminar una ruta que ya tiene entregas registradas.")


def eliminar_ruta(ruta, usuario_id, origen=None):
    """Elimina la ruta; sus pedidos vuelven a pendientes con su evento. No hace commit.

    Devuelve (codigo, pedidos devueltos).
    """
    verificar_eliminar_ruta(ruta)
    codigo, devueltos = ruta.codigo, 0
    for pedido in list(ruta.pedidos):
        anterior = pedido.estado
        pedido.ruta_id = None
        pedido.orden_en_ruta = None
        pedido.estado = EstadoPedido.PENDIENTE
        db.session.add(EventoPedido(
            pedido_id=pedido.id, usuario_id=usuario_id,
            estado_anterior=anterior, estado_nuevo=EstadoPedido.PENDIENTE,
            nota=_nota(f"Ruta {codigo} eliminada", origen),
        ))
        devueltos += 1
    db.session.delete(ruta)
    return codigo, devueltos
