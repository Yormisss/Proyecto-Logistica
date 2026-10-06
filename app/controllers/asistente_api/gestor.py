"""Funciones del asistente de voz del gestor logistico (DESPACHADOR).

El ADMIN tambien puede usarlas. Las consultas leen la operacion del dia; las
acciones piden confirmacion y aplican los mismos servicios que la pantalla
(pedidos, despacho, inventario y planificacion), por lo que disparan los
mismos avisos de Make.

Fuera del alcance por voz: importar CSV, crear o eliminar rutas, ajustes de
inventario (solo el admin, en admin.py) y crear clientes o sedes.
"""

from flask import g
from sqlalchemy.orm import joinedload

from app.controllers.asistente_api import (
    NOTA_ASISTENTE, cantidad, exigir_confirmacion, funcion_asistente, responder,
)
from app.controllers.asistente_api.comun import (
    LARGO_MAXIMO_MOTIVO, entero, enumerar, fecha_iso, fecha_voz, pedido_texto, porcentaje,
    resolver, texto,
)
from app.extensions import db
from app.models import EstadoPedido, EstadoRuta, Pedido, Rol, Ruta, TipoMovimiento
from app.services import analitica
from app.services.busqueda_voz import (
    buscar_clientes, buscar_conductores, buscar_pedidos, buscar_productos, buscar_sedes,
)
from app.services.despacho import (
    PRIORIDADES, TransicionInvalida, anular_pedido, cambiar_prioridad, reintentar_entrega,
    ruta_del_dia, ruta_finalizada_del_dia, validar_transicion, verificar_anulacion,
    verificar_cambio_prioridad, verificar_reintento,
)
from app.services.inventario import registrar_movimiento
from app.services.pedidos import (
    PRIORIDAD_POR_DEFECTO, PedidoInvalido, crear_pedido, destino_de_sede, validar_pedido,
)
from app.services.planificacion import (
    PlanificacionInvalida, agregar_a_ruta, verificar_agregar_a_ruta,
)
from app.services.solicitudes import pendientes as pendientes_de_contacto
from app.tiempo import hoy

ESPACIO = "gestor"
ROLES = (Rol.DESPACHADOR, Rol.ADMIN)

# Elementos que se leen como maximo en una lista hablada.
LIMITE_LISTA = 5

PARAMETRO_PEDIDO = {
    "pedido": {"type": "string",
               "description": "Codigo del pedido, su numero del dia (\"el 5 de hoy\") o el "
                              "nombre del cliente."},
}
PARAMETRO_PRODUCTO = {
    "producto": {"type": "string", "description": "SKU o nombre del producto."},
}


def _pedido():
    """Pedido del argumento `pedido`, o (None, mensaje)."""
    buscado = texto("pedido")
    if not buscado:
        return None, "Necesito el código del pedido o el nombre del cliente."
    return resolver(buscar_pedidos(buscado), pedido_texto, "pedidos",
                    f"No encontré ningún pedido para {buscado}.")


def _producto(solo_activos=True):
    buscado = texto("producto")
    if not buscado:
        return None, "Necesito el SKU o el nombre del producto."
    return resolver(buscar_productos(buscado, solo_activos=solo_activos),
                    lambda p: f"{p.sku}, {p.nombre}", "productos",
                    f"No encontré el producto {buscado}.")


def _prioridad_texto(prioridad):
    return PRIORIDADES.get(prioridad, str(prioridad)).lower()


def _lista(elementos, como_texto):
    """Primeros LIMITE_LISTA elementos leidos, y cuantos faltan."""
    leidos = [como_texto(e) for e in elementos[:LIMITE_LISTA]]
    sobrantes = len(elementos) - len(leidos)
    return enumerar(leidos) + (f", y {sobrantes} más" if sobrantes else "")


# --------------------------------------------------------------------------
# Consultas
# --------------------------------------------------------------------------

@funcion_asistente(
    ESPACIO, "resumen-dia", roles=ROLES,
    descripcion="Indicadores de hoy: pedidos totales, entregados, fallidos, cancelados, "
                "pendientes (sin asignar y en ruta) y tasa de exito.",
)
def resumen_dia():
    kpis = analitica.kpis_del_dia()
    if not kpis["total_dia"]:
        return responder("Hoy no hay pedidos con fecha de despacho.")
    return responder(
        f"Hoy hay {cantidad(kpis['total_dia'], 'pedido')}: "
        f"{cantidad(kpis['entregados'], 'entregado')}, "
        f"{cantidad(kpis['fallidos'], 'fallido')}, "
        f"{cantidad(kpis['cancelados'], 'cancelado')} y "
        f"{cantidad(kpis['pendientes'], 'pendiente')}, de los cuales "
        f"{kpis['sin_asignar']} sin asignar y {kpis['en_ruta']} en ruta. "
        f"La tasa de éxito es {porcentaje(kpis['porcentaje_exito'])}."
    )


@funcion_asistente(
    ESPACIO, "pendientes-sin-ruta", roles=ROLES,
    descripcion="Pedidos pendientes que aun no tienen ruta: los de hoy por prioridad y "
                "cuantos hay de otras fechas.",
)
def pendientes_sin_ruta():
    fecha = hoy()
    pendientes = (
        db.session.query(Pedido)
        .filter(Pedido.estado == EstadoPedido.PENDIENTE, Pedido.ruta_id.is_(None))
        .order_by(Pedido.fecha_despacho, Pedido.prioridad, Pedido.codigo)
        .all()
    )
    de_hoy = [p for p in pendientes if p.fecha_despacho == fecha]
    otros = len(pendientes) - len(de_hoy)
    if not pendientes:
        return responder("No hay pedidos pendientes sin ruta.")

    partes = []
    if de_hoy:
        partes.append(
            f"Hoy hay {cantidad(len(de_hoy), 'pedido pendiente', 'pedidos pendientes')} "
            f"sin ruta: "
            + _lista(de_hoy, lambda p: f"{pedido_texto(p)}, prioridad {_prioridad_texto(p.prioridad)}")
            + "."
        )
    else:
        partes.append("Hoy no quedan pedidos pendientes sin ruta.")
    if otros:
        partes.append(f"Además hay {cantidad(otros, 'pendiente')} de otras fechas.")
    return responder(" ".join(partes))


@funcion_asistente(
    ESPACIO, "avance-rutas", roles=ROLES,
    descripcion="Avance de las rutas de hoy por conductor: estado y paradas cerradas.",
)
def avance_rutas():
    rutas = (
        db.session.query(Ruta)
        .options(joinedload(Ruta.pedidos), joinedload(Ruta.conductor))
        .filter(Ruta.fecha == hoy(), Ruta.estado != EstadoRuta.CANCELADA)
        .order_by(Ruta.codigo)
        .all()
    )
    if not rutas:
        return responder("Hoy no hay rutas planificadas.")
    detalle = [
        f"{r.conductor.nombre}, {r.codigo}, {r.estado_etiqueta.lower()}: "
        f"{r.paradas_cerradas} de {cantidad(r.total_paradas, 'parada')} cerradas"
        for r in rutas
    ]
    return responder(f"Hoy hay {cantidad(len(rutas), 'ruta')}. " + "; ".join(detalle) + ".")


@funcion_asistente(
    ESPACIO, "buscar-pedido", roles=ROLES,
    descripcion="Estado de un pedido: cliente, fecha, prioridad, ruta y conductor, y sus "
                "ultimos movimientos en la bitacora.",
    parametros=PARAMETRO_PEDIDO, requeridos=("pedido",),
)
def buscar_pedido():
    pedido, error = _pedido()
    if pedido is None:
        return responder(error)

    partes = [
        f"El pedido {pedido_texto(pedido)} está {pedido.estado_etiqueta.lower()}, "
        f"para el {fecha_voz(pedido.fecha_despacho)}, prioridad {_prioridad_texto(pedido.prioridad)}."
    ]
    if pedido.ruta is not None:
        partes.append(
            f"Va en la ruta {pedido.ruta.codigo} de {pedido.ruta.conductor.nombre}, "
            f"parada {pedido.orden_en_ruta}."
        )
    else:
        partes.append("No tiene ruta asignada.")
    if pedido.prueba_entrega and pedido.prueba_entrega.motivo_fallo:
        partes.append(f"Motivo del fallo: {pedido.prueba_entrega.motivo_fallo}.")

    eventos = sorted(pedido.eventos, key=lambda e: e.id, reverse=True)[:2]
    if eventos:
        partes.append(
            "Últimos movimientos: "
            + "; ".join(
                f"{e.registrado_en.strftime('%d/%m %H:%M')}, "
                f"{EstadoPedido.ETIQUETAS.get(e.estado_nuevo, e.estado_nuevo).lower()}"
                + (f", {e.nota}" if e.nota else "")
                for e in eventos
            )
            + "."
        )
    return responder(" ".join(partes))


@funcion_asistente(
    ESPACIO, "stock-producto", roles=ROLES,
    descripcion="Stock actual y minimo de un producto.",
    parametros=PARAMETRO_PRODUCTO, requeridos=("producto",),
)
def stock_producto():
    producto, error = _producto(solo_activos=False)
    if producto is None:
        return responder(error)

    estado = ""
    if producto.stock_actual < 0:
        estado = " Está en negativo: hay un descuadre con el inventario físico."
    elif producto.bajo_minimo:
        estado = " Está en o por debajo del mínimo."
    if not producto.activo:
        estado += " El producto está inactivo."
    return responder(
        f"{producto.sku}, {producto.nombre}: "
        f"{cantidad(producto.stock_actual, 'unidad', 'unidades')}; "
        f"el mínimo es {producto.stock_minimo}.{estado}"
    )


@funcion_asistente(
    ESPACIO, "productos-bajo-minimo", roles=ROLES,
    descripcion="Productos activos en o por debajo de su stock minimo, el mas critico primero.",
)
def productos_bajo_minimo():
    productos = analitica.productos_bajo_minimo()
    if not productos:
        return responder("Ningún producto está bajo el mínimo.")
    return responder(
        f"Hay {cantidad(len(productos), 'producto')} bajo el mínimo: "
        + _lista(productos, lambda p: f"{p.sku}, {p.nombre}, con {p.stock_actual} "
                                      f"y mínimo {p.stock_minimo}")
        + "."
    )


@funcion_asistente(
    ESPACIO, "fallidos-hoy", roles=ROLES,
    descripcion="Entregas fallidas de hoy con su motivo.",
)
def fallidos_hoy():
    fallidos = (
        db.session.query(Pedido)
        .options(joinedload(Pedido.prueba_entrega))
        .filter(Pedido.estado == EstadoPedido.FALLIDO, Pedido.fecha_despacho == hoy())
        .order_by(Pedido.codigo)
        .all()
    )
    if not fallidos:
        return responder("Hoy no hay entregas fallidas.")

    def describir_fallo(p):
        motivo = p.prueba_entrega.motivo_fallo if p.prueba_entrega else None
        return pedido_texto(p) + (f", por {motivo}" if motivo else ", sin motivo registrado")

    return responder(
        f"Hoy hay {cantidad(len(fallidos), 'entrega fallida', 'entregas fallidas')}: "
        + _lista(fallidos, describir_fallo) + "."
    )


@funcion_asistente(
    ESPACIO, "solicitudes-contacto-pendientes", roles=ROLES,
    descripcion="Clientes que pidieron por voz que el gestor los contacte y aun no han sido "
                "atendidos, el mas antiguo primero.",
)
def solicitudes_contacto_pendientes():
    solicitudes = pendientes_de_contacto()
    if not solicitudes:
        return responder("No hay solicitudes de contacto pendientes.")

    def solicitud_texto(s):
        medio = s.telefono or s.correo or "sin teléfono ni correo"
        return f"{s.cliente.nombre}, el {s.creada_en.strftime('%d/%m a las %H:%M')}, por {s.motivo}, contacto {medio}"

    return responder(
        f"Hay {cantidad(len(solicitudes), 'solicitud de contacto pendiente', 'solicitudes de contacto pendientes')}: "
        + _lista(solicitudes, solicitud_texto)
        + ". Se marcan como atendidas en pantalla."
    )


# --------------------------------------------------------------------------
# Acciones (con confirmacion)
# --------------------------------------------------------------------------

@funcion_asistente(
    ESPACIO, "crear-pedido", roles=ROLES, accion=True,
    descripcion="Crea un pedido para un cliente registrado y una de sus sedes registradas, "
                "con productos existentes. No crea clientes ni sedes.",
    parametros={
        "cliente": {"type": "string", "description": "Nombre del cliente registrado."},
        "sede": {"type": "string",
                 "description": "Etiqueta o direccion de la sede. Opcional si el cliente tiene "
                                "una sola."},
        "productos": {
            "type": "array",
            "description": "Productos del pedido.",
            "items": {
                "type": "object",
                "properties": {
                    "producto": {"type": "string", "description": "SKU o nombre."},
                    "cantidad": {"type": "integer", "description": "Unidades, entero positivo."},
                },
                "required": ["producto", "cantidad"],
            },
        },
        "fecha": {"type": "string",
                  "description": "Fecha de despacho AAAA-MM-DD. Por defecto, hoy."},
        "prioridad": {"type": "integer", "description": "1 alta, 2 media, 3 baja (por defecto)."},
    },
    requeridos=("cliente", "productos"),
)
def crear_pedido_voz():
    nombre_cliente = texto("cliente")
    if not nombre_cliente:
        return responder("Necesito el nombre del cliente.")
    cliente, error = resolver(
        buscar_clientes(nombre_cliente), lambda c: c.nombre, "clientes",
        f"No encontré el cliente {nombre_cliente}. Por voz solo se crean pedidos para "
        "clientes registrados.",
    )
    if cliente is None:
        return responder(error)

    def sede_texto(s):
        return f"{s.etiqueta}, en {s.direccion}"

    sedes = buscar_sedes(cliente, texto("sede"))
    if sedes.vacio and not [s for s in cliente.direcciones if s.activa]:
        return responder(f"{cliente.nombre} no tiene sedes registradas; regístrela en pantalla.")
    sede, error = resolver(
        sedes, sede_texto, f"sedes de {cliente.nombre}",
        f"No encontré esa sede de {cliente.nombre}. Sus sedes son: "
        + enumerar([sede_texto(s) for s in cliente.direcciones if s.activa]) + ".",
    )
    if sede is None:
        return responder(error)

    lineas = g.argumentos.get("productos")
    if not isinstance(lineas, list) or not lineas:
        return responder("Necesito al menos un producto con su cantidad.")
    items, leidos = [], []
    for linea in lineas:
        if not isinstance(linea, dict):
            return responder("No entendí la lista de productos.")
        nombre = str(linea.get("producto") or "").strip()
        unidades = entero(linea.get("cantidad"))
        if not nombre:
            return responder("Falta el nombre de uno de los productos.")
        if unidades is None or unidades <= 0:
            return responder(f"La cantidad de {nombre} debe ser un número entero mayor que cero.")
        producto, error = resolver(
            buscar_productos(nombre), lambda p: f"{p.sku}, {p.nombre}",
            f"productos para {nombre}", f"No encontré el producto {nombre}.",
        )
        if producto is None:
            return responder(error)
        items.append((producto.id, unidades))
        leidos.append(f"{unidades} de {producto.nombre}")

    fecha = fecha_iso(g.argumentos.get("fecha"), hoy())
    if fecha is None:
        return responder("No entendí la fecha; dímela como año, mes y día.")
    prioridad = entero(g.argumentos.get("prioridad"))
    if g.argumentos.get("prioridad") in (None, ""):
        prioridad = PRIORIDAD_POR_DEFECTO

    destino = destino_de_sede(cliente, sede)
    _, errores = validar_pedido(
        items=items, fecha_despacho=fecha, prioridad=prioridad,
        ventana_inicio=destino["ventana_inicio"], ventana_fin=destino["ventana_fin"],
    )
    if errores:
        return responder("No puedo crear el pedido: " + " ".join(m for _, m in errores))

    pendiente = exigir_confirmacion(
        f"Voy a crear un pedido para {cliente.nombre}, sede {sede_texto(sede)}, para el "
        f"{fecha_voz(fecha)} con prioridad {_prioridad_texto(prioridad)}: "
        f"{enumerar(leidos)}."
    )
    if pendiente:
        return pendiente

    try:
        pedido = crear_pedido(
            cliente=cliente, direccion=sede, destino=destino, items=items,
            fecha_despacho=fecha, prioridad=prioridad, usuario_id=g.usuario.id,
            nota=NOTA_ASISTENTE,
        )
    except PedidoInvalido as error:
        db.session.rollback()
        return responder(f"No pude crear el pedido: {error}")
    db.session.commit()
    return responder(f"Listo, creé el pedido {pedido.codigo} para {cliente.nombre}.")


@funcion_asistente(
    ESPACIO, "anular-pedido", roles=ROLES, accion=True,
    descripcion="Anula un pedido pendiente, asignado o fallido, con el motivo.",
    parametros={**PARAMETRO_PEDIDO,
                "motivo": {"type": "string", "description": "Motivo de la anulacion."}},
    requeridos=("pedido", "motivo"),
)
def anular_pedido_voz():
    pedido, error = _pedido()
    if pedido is None:
        return responder(error)
    motivo = texto("motivo")[:LARGO_MAXIMO_MOTIVO]
    if not motivo:
        return responder("Necesito el motivo de la anulación.")
    try:
        verificar_anulacion(pedido)
    except TransicionInvalida as causa:
        return responder(f"No puedo anular el pedido {pedido.codigo}. {causa}")

    pendiente = exigir_confirmacion(
        f"Voy a anular el pedido {pedido_texto(pedido)} por: {motivo}."
    )
    if pendiente:
        return pendiente

    try:
        anular_pedido(pedido, g.usuario.id, motivo, origen=NOTA_ASISTENTE)
    except TransicionInvalida as causa:
        db.session.rollback()
        return responder(f"No pude anular el pedido {pedido.codigo}. {causa}")
    db.session.commit()
    return responder(f"Listo, anulé el pedido {pedido.codigo}.")


@funcion_asistente(
    ESPACIO, "reintentar-pedido", roles=ROLES, accion=True,
    descripcion="Devuelve un pedido fallido de una ruta de hoy a asignado, para un nuevo intento "
                "del conductor.",
    parametros=PARAMETRO_PEDIDO, requeridos=("pedido",),
)
def reintentar_pedido():
    pedido, error = _pedido()
    if pedido is None:
        return responder(error)
    try:
        verificar_reintento(pedido)
        validar_transicion(pedido.estado, EstadoPedido.ASIGNADO)
    except TransicionInvalida as causa:
        return responder(f"No puedo reintentar el pedido {pedido.codigo}. {causa}")

    pendiente = exigir_confirmacion(
        f"Voy a devolver el pedido {pedido_texto(pedido)} a la ruta {pedido.ruta.codigo} "
        f"de {pedido.ruta.conductor.nombre} para un nuevo intento."
    )
    if pendiente:
        return pendiente

    try:
        reintentar_entrega(pedido, g.usuario.id, nota=NOTA_ASISTENTE)
    except TransicionInvalida as causa:
        db.session.rollback()
        return responder(f"No pude reintentar el pedido {pedido.codigo}. {causa}")
    db.session.commit()
    return responder(f"Listo, el pedido {pedido.codigo} quedó asignado para un nuevo intento.")


@funcion_asistente(
    ESPACIO, "registrar-entrada", roles=ROLES, accion=True,
    descripcion="Registra una entrada de mercancia (suma unidades al stock de un producto).",
    parametros={**PARAMETRO_PRODUCTO,
                "cantidad": {"type": "integer", "description": "Unidades recibidas, entero positivo."},
                "motivo": {"type": "string", "description": "Opcional: remision o proveedor."}},
    requeridos=("producto", "cantidad"),
)
def registrar_entrada():
    producto, error = _producto(solo_activos=False)
    if producto is None:
        return responder(error)
    unidades = entero(g.argumentos.get("cantidad"))
    if unidades is None or unidades <= 0:
        return responder("La cantidad debe ser un número entero mayor que cero.")
    motivo = texto("motivo")[:LARGO_MAXIMO_MOTIVO]

    pendiente = exigir_confirmacion(
        f"Voy a registrar una entrada de {cantidad(unidades, 'unidad', 'unidades')} de "
        f"{producto.sku}, {producto.nombre}; el stock pasa de {producto.stock_actual} a "
        f"{producto.stock_actual + unidades}."
    )
    if pendiente:
        return pendiente

    registrar_movimiento(
        producto, TipoMovimiento.ENTRADA, unidades, g.usuario.id,
        motivo=f"{motivo}. {NOTA_ASISTENTE}" if motivo else NOTA_ASISTENTE,
    )
    db.session.commit()
    return responder(f"Listo, registré la entrada de {unidades} de {producto.nombre}.")


@funcion_asistente(
    ESPACIO, "cambiar-prioridad", roles=ROLES, accion=True,
    descripcion="Cambia la prioridad de un pedido pendiente o asignado (1 alta, 2 media, 3 baja).",
    parametros={**PARAMETRO_PEDIDO,
                "prioridad": {"type": "integer", "description": "1 alta, 2 media, 3 baja."}},
    requeridos=("pedido", "prioridad"),
)
def cambiar_prioridad_voz():
    pedido, error = _pedido()
    if pedido is None:
        return responder(error)
    prioridad = entero(g.argumentos.get("prioridad"))
    try:
        verificar_cambio_prioridad(pedido, prioridad)
    except TransicionInvalida as causa:
        return responder(f"No puedo cambiar la prioridad del pedido {pedido.codigo}. {causa}")

    pendiente = exigir_confirmacion(
        f"Voy a cambiar la prioridad del pedido {pedido_texto(pedido)} de "
        f"{_prioridad_texto(pedido.prioridad)} a {_prioridad_texto(prioridad)}."
    )
    if pendiente:
        return pendiente

    try:
        cambiar_prioridad(pedido, prioridad, g.usuario.id, origen=NOTA_ASISTENTE)
    except TransicionInvalida as causa:
        db.session.rollback()
        return responder(f"No pude cambiar la prioridad. {causa}")
    db.session.commit()
    return responder(
        f"Listo, el pedido {pedido.codigo} quedó con prioridad {_prioridad_texto(prioridad)}."
    )


@funcion_asistente(
    ESPACIO, "agregar-a-ruta", roles=ROLES, accion=True,
    descripcion="Agrega un pedido pendiente a la ruta de hoy de un conductor y recalcula la "
                "secuencia. No crea rutas ni reabre una ruta finalizada.",
    parametros={**PARAMETRO_PEDIDO,
                "conductor": {"type": "string", "description": "Nombre del conductor."}},
    requeridos=("pedido", "conductor"),
)
def agregar_a_ruta_voz():
    pedido, error = _pedido()
    if pedido is None:
        return responder(error)
    nombre = texto("conductor")
    if not nombre:
        return responder("Necesito el nombre del conductor.")
    conductor, error = resolver(buscar_conductores(nombre), lambda c: c.nombre, "conductores",
                                f"No encontré un conductor activo llamado {nombre}.")
    if conductor is None:
        return responder(error)

    ruta = ruta_del_dia(conductor.id)
    if ruta is None:
        finalizada = ruta_finalizada_del_dia(conductor.id)
        if finalizada is not None:
            return responder(
                f"La ruta de hoy de {conductor.nombre}, la {finalizada.codigo}, ya está finalizada "
                "y no se reabre por voz. Para asignarle más pedidos hoy, cree una ruta nueva en "
                "pantalla."
            )
        return responder(
            f"{conductor.nombre} no tiene ruta hoy. Crear rutas se hace en pantalla."
        )
    try:
        verificar_agregar_a_ruta(pedido, ruta)
    except PlanificacionInvalida as causa:
        return responder(str(causa))

    pendiente = exigir_confirmacion(
        f"Voy a agregar el pedido {pedido_texto(pedido)} a la ruta {ruta.codigo} de "
        f"{conductor.nombre} y recalcular la secuencia."
    )
    if pendiente:
        return pendiente

    try:
        agregar_a_ruta(pedido, ruta, g.usuario.id, origen=NOTA_ASISTENTE)
    except PlanificacionInvalida as causa:
        db.session.rollback()
        return responder(str(causa))
    db.session.commit()
    return responder(
        f"Listo, el pedido {pedido.codigo} quedó como parada {pedido.orden_en_ruta} de "
        f"{ruta.total_paradas} en la ruta {ruta.codigo}."
    )
