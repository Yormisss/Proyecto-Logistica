"""Funciones del asistente de voz del gestor logistico (DESPACHADOR).

El ADMIN tambien puede usarlas. Las consultas leen la operacion del dia y los
indicadores de la analitica; las acciones piden confirmacion y aplican los
mismos servicios que la pantalla (pedidos, despacho, inventario, planificacion
y solicitudes de contacto), por lo que disparan los mismos avisos de Make.

Las acciones se agrupan por entidad con el parametro `operacion` (`pedidos`,
`productos`, `rutas`). El ajuste de inventario es de ambos roles, con motivo
obligatorio.

Fuera del alcance por voz: importar CSV.
"""

from flask import g
from sqlalchemy.orm import joinedload, selectinload

from app.controllers.asistente_api import (
    NOTA_ASISTENTE, cantidad, exigir_confirmacion, funcion_agrupada, funcion_asistente, responder,
)
from app.controllers.asistente_api.comun import (
    LARGO_MAXIMO_MOTIVO, dichos, entero, enumerar, fecha_iso, fecha_larga, fecha_voz,
    frase_datos_invalidos, me_falta, pedido_texto, porcentaje, resolver, texto, valor_leido,
)
from app.extensions import db
from app.models import (
    DireccionCliente, EstadoPedido, EstadoRuta, Pedido, Rol, Ruta, TipoMovimiento, normalizar_texto,
)
from app.services import analitica
from app.services.busqueda_voz import (
    buscar_clientes, buscar_conductores, buscar_pedidos, buscar_productos, buscar_rutas, buscar_sedes,
    buscar_vehiculos,
)
from app.services.despacho import (
    PRIORIDADES, TransicionInvalida, anular_pedido, reintentar_entrega, ruta_del_dia,
    ruta_finalizada_del_dia, validar_transicion, verificar_anulacion, verificar_reintento,
)
from app.services.inventario import registrar_movimiento
from app.services.pedidos import (
    PRIORIDAD_POR_DEFECTO, PedidoInvalido, campos_editables, crear_pedido, destino_de_sede,
    editar_pedido, validar_edicion, validar_pedido,
)
from app.services.planificacion import (
    PlanificacionInvalida, agregar_a_ruta, crear_ruta, eliminar_ruta, quitar_parada,
    recalcular_secuencia, validar_ruta, verificar_agregar_a_ruta, verificar_eliminar_ruta,
    verificar_quitar_parada,
)
from app.services.ruteo import ESTRATEGIA_DISTANCIA, ESTRATEGIA_VENTANA, ESTRATEGIAS
from app.services.validacion import DatosInvalidos
from app.services.solicitudes import SolicitudInvalida, marcar_atendida, pendiente_de
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


def _sede_texto(sede):
    return f"{sede.etiqueta}, en {sede.direccion}"


def _leer_productos(lineas):
    """(items, lo que se lee, error) de la lista de productos dicha por voz.

    `items` es [(producto_id, cantidad)], como lo esperan los servicios.
    """
    items, leidos = [], []
    for linea in lineas:
        if not isinstance(linea, dict):
            return None, None, "No entendí la lista de productos."
        nombre = str(linea.get("producto") or "").strip()
        unidades = entero(linea.get("cantidad"))
        if not nombre:
            return None, None, "Falta el nombre de uno de los productos."
        if unidades is None or unidades <= 0:
            return None, None, f"La cantidad de {nombre} debe ser un número entero mayor que cero."
        producto, error = resolver(
            buscar_productos(nombre), lambda p: f"{p.sku}, {p.nombre}",
            f"productos para {nombre}", f"No encontré el producto {nombre}.",
        )
        if producto is None:
            return None, None, error
        items.append((producto.id, unidades))
        leidos.append(f"{unidades} de {producto.nombre}")
    return items, leidos, None


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
        .options(selectinload(Pedido.pruebas_entrega))
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
        + ". Para marcar una como atendida, dime de qué cliente es."
    )


@funcion_asistente(
    ESPACIO, "atender-solicitud", roles=ROLES, accion=True, entidad="solicitud",
    descripcion="Marca como atendida la solicitud de contacto pendiente de un cliente, como en la "
                "pantalla Solicitudes.",
    parametros={"cliente": {"type": "string",
                            "description": "Nombre del cliente que pidio que lo contactaran."}},
    requeridos=("cliente",),
)
def atender_solicitud():
    buscado = texto("cliente")
    if not buscado:
        return responder(me_falta(["el cliente"]))
    cliente, error = resolver(buscar_clientes(buscado), lambda c: c.nombre, "clientes",
                              f"No encontré el cliente {buscado}.")
    if cliente is None:
        return responder(error)
    solicitud = pendiente_de(cliente)
    if solicitud is None:
        return responder(f"{cliente.nombre} no tiene una solicitud de contacto pendiente.")

    pendiente = exigir_confirmacion(
        f"Voy a marcar como atendida la solicitud de {cliente.nombre} del "
        f"{solicitud.creada_en.strftime('%d/%m a las %H:%M')}, por {solicitud.motivo}."
    )
    if pendiente:
        return pendiente

    try:
        marcar_atendida(solicitud, g.usuario.id)
    except SolicitudInvalida as error:
        db.session.rollback()
        return responder(str(error))
    db.session.commit()
    return responder(f"Listo, la solicitud de contacto de {cliente.nombre} quedó atendida.")


# --------------------------------------------------------------------------
# Indicadores de la analitica, con los mismos periodos que el panel. El panel
# de rendimiento (tiempos de respuesta) es solo del admin: admin.py.
# --------------------------------------------------------------------------

PERIODOS_DIAS = (7, 14, 30)
DIAS_POR_DEFECTO = 14

PARAMETRO_DIAS = {
    "dias": {"type": "integer",
             "description": "Periodo en dias: 7, 14 (por defecto) o 30, como en el panel."},
}


def _dias():
    dias = entero(g.argumentos.get("dias"))
    return dias if dias in PERIODOS_DIAS else DIAS_POR_DEFECTO


def _minutos(valor):
    return f"{valor:.0f} minutos" if valor is not None else "sin datos"


@funcion_asistente(
    ESPACIO, "tiempo-promedio-entrega", roles=ROLES,
    descripcion="Tiempo promedio desde que una parada sale en camino hasta que se entrega.",
    parametros=PARAMETRO_DIAS,
)
def tiempo_promedio_entrega():
    dias = _dias()
    datos = analitica.tiempo_promedio_entrega(dias)
    if not datos["muestras"]:
        return responder(f"En los últimos {dias} días no hay entregas con tiempos registrados.")
    return responder(
        f"En los últimos {dias} días, una entrega tarda en promedio "
        f"{_minutos(datos['promedio_min'])} desde que sale en camino, sobre "
        f"{cantidad(datos['muestras'], 'entrega')}; la más rápida "
        f"{_minutos(datos['minimo'])} y la más lenta {_minutos(datos['maximo'])}."
    )


@funcion_asistente(
    ESPACIO, "cumplimiento-ventana", roles=ROLES,
    descripcion="Porcentaje de entregas hechas dentro de la ventana horaria pactada.",
    parametros=PARAMETRO_DIAS,
)
def cumplimiento_ventana():
    dias = _dias()
    datos = analitica.cumplimiento_ventana(dias)
    if not datos["total"]:
        return responder(f"En los últimos {dias} días no hay entregas con ventana horaria.")
    return responder(
        f"En los últimos {dias} días, {porcentaje(datos['porcentaje'])} de las entregas con "
        f"ventana se hizo a tiempo: {datos['dentro']} de {datos['total']}."
    )


@funcion_asistente(
    ESPACIO, "productividad-conductores", roles=ROLES,
    descripcion="Entregas y tasa de exito por conductor, del mas productivo al menos.",
    parametros=PARAMETRO_DIAS,
)
def productividad_conductores():
    dias = _dias()
    filas = analitica.productividad_conductores(dias)
    if not filas:
        return responder(f"En los últimos {dias} días no hay entregas cerradas por conductores.")
    return responder(
        f"En los últimos {dias} días: "
        + enumerar([
            f"{f['conductor']}, {cantidad(f['entregadas'], 'entregada')} y "
            f"{cantidad(f['fallidas'], 'fallida')}, éxito {porcentaje(f['exito'])}"
            for f in filas[:LIMITE_LISTA]
        ])
        + "."
    )


@funcion_asistente(
    ESPACIO, "causas-fallo", roles=ROLES,
    descripcion="Motivos de entrega fallida, del mas frecuente al menos.",
    parametros=PARAMETRO_DIAS,
)
def causas_fallo():
    dias = _dias()
    filas = analitica.motivos_fallo(dias)
    if not filas:
        return responder(f"En los últimos {dias} días no hay entregas fallidas con motivo.")
    total = sum(f["cantidad"] for f in filas)
    partes = [
        f"En los últimos {dias} días hubo {cantidad(total, 'entrega fallida', 'entregas fallidas')}. "
        "Las causas principales: "
        + enumerar([f"{f['motivo']}, {f['cantidad']}" for f in filas[:LIMITE_LISTA]])
        + "."
    ]
    otro = next((f for f in filas if f.get("es_otro") and f.get("detalles")), None)
    if otro:
        partes.append(
            f"En «{otro['motivo']}» se escribió: "
            + enumerar([f"{d['texto']}, {d['cantidad']}" for d in otro["detalles"][:LIMITE_LISTA]])
            + "."
        )
    return responder(" ".join(partes))


# --------------------------------------------------------------------------
# Acciones (con confirmacion)
# --------------------------------------------------------------------------

pedidos = funcion_agrupada(
    ESPACIO, "pedidos", roles=ROLES, entidad="pedido",
    descripcion="Pedidos: crear, editar, anular o reintentar uno fallido. Para consultar un "
                "pedido usa buscar-pedido.",
    parametros={
        **PARAMETRO_PEDIDO,
        "cliente": {"type": "string", "description": "crear: nombre del cliente registrado."},
        "sede": {"type": "string",
                 "description": "crear y editar: etiqueta o direccion de la sede. Al crear, "
                                "opcional si el cliente tiene una sola."},
        "productos": {
            "type": "array",
            "description": "crear: productos del pedido. editar: la lista completa nueva, que "
                           "reemplaza la anterior.",
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
                  "description": "crear y editar: fecha de despacho AAAA-MM-DD, no anterior a hoy. "
                                 "Al crear, por defecto hoy."},
        "prioridad": {"type": "integer",
                      "description": "crear y editar: 1 alta, 2 media, 3 baja (por defecto al "
                                     "crear)."},
        "ventana_inicio": {"type": "string", "description": "editar: inicio de la ventana, HH:MM."},
        "ventana_fin": {"type": "string", "description": "editar: fin de la ventana, HH:MM."},
        "observaciones": {"type": "string", "description": "editar: observaciones del pedido."},
        "motivo": {"type": "string", "description": "anular: motivo de la anulacion."},
    },
)


@pedidos("crear", "Crear pedido",
         "crea un pedido para un cliente registrado y una de sus sedes registradas, con "
         "productos existentes; requiere cliente y productos")
def crear_pedido_voz():
    # Dialogo de formulario: se dice de una vez todo lo obligatorio que falta.
    nombre_cliente = texto("cliente")
    lineas = g.argumentos.get("productos")
    faltan = ([] if nombre_cliente else ["el cliente"]) + \
        ([] if isinstance(lineas, list) and lineas else ["los productos con sus cantidades"])
    if faltan:
        return responder(me_falta(faltan))
    cliente, error = resolver(
        buscar_clientes(nombre_cliente), lambda c: c.nombre, "clientes",
        f"No encontré el cliente {nombre_cliente}: los pedidos se crean para clientes registrados. "
        "¿Quieres que lo registre ahora? Después retomamos el pedido con lo que ya me dijiste.",
    )
    if cliente is None:
        return responder(error)

    sedes = buscar_sedes(cliente, texto("sede"))
    if sedes.vacio and not [s for s in cliente.direcciones if s.activa]:
        return responder(f"{cliente.nombre} no tiene sedes registradas. ¿Quieres que le agregue una? "
                         "Después retomamos el pedido.")
    sede, error = resolver(
        sedes, _sede_texto, f"sedes de {cliente.nombre}",
        f"No encontré esa sede de {cliente.nombre}. Sus sedes son: "
        + enumerar([_sede_texto(s) for s in cliente.direcciones if s.activa])
        + ". Si es una sede nueva, puedo agregarla.",
    )
    if sede is None:
        return responder(error)

    items, leidos, error = _leer_productos(lineas)
    if error:
        return responder(error)

    fecha = fecha_iso(g.argumentos.get("fecha"), hoy())
    if fecha is None:
        return responder("No entendí la fecha; dímela como año, mes y día.")
    if fecha < hoy():
        return responder(
            "No puedo crear pedidos con fecha pasada por voz; la fecha más temprana es hoy, "
            f"{fecha_larga(hoy())}."
        )
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
        f"Voy a crear un pedido para {cliente.nombre}, sede {_sede_texto(sede)}, para el "
        f"{fecha_larga(fecha)} con prioridad {_prioridad_texto(prioridad)}: "
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


@pedidos("anular", "Anular pedido",
         "anula un pedido pendiente, asignado o fallido; requiere pedido y motivo")
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


@pedidos("reintentar", "Reintentar pedido",
         "devuelve un pedido fallido de una ruta de hoy a asignado, para un nuevo intento del "
         "conductor; requiere pedido")
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
        + (f" La ruta {pedido.ruta.codigo} está finalizada y se reabrirá."
           if pedido.ruta.estado == EstadoRuta.FINALIZADA else "")
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


productos = funcion_agrupada(
    ESPACIO, "productos", roles=ROLES, entidad="producto",
    descripcion="Productos del inventario. El stock solo cambia con 'entrada' o 'ajuste'. Para "
                "consultar el stock usa stock-producto.",
    parametros={
        **PARAMETRO_PRODUCTO,
        "cantidad": {"type": "integer",
                     "description": "entrada: unidades recibidas, entero positivo."},
        "nuevo_stock": {"type": "integer",
                        "description": "ajuste: stock contado, entero mayor o igual a cero."},
        "motivo": {"type": "string",
                   "description": "entrada: opcional (remision o proveedor). ajuste: obligatorio."},
    },
)


@productos("entrada", "Registrar entrada",
           "suma unidades recibidas al stock; requiere producto y cantidad", avisar=False)
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


@productos("ajuste", "Ajustar inventario",
           "fija el stock de un producto al valor contado (inventario fisico); requiere "
           "producto, nuevo_stock y motivo; puede disparar el aviso de stock bajo")
def ajustar_inventario():
    producto, error = _producto(solo_activos=False)
    if producto is None:
        return responder(error)
    motivo = texto("motivo")[:LARGO_MAXIMO_MOTIVO]
    faltan = (["el nuevo stock"] if g.argumentos.get("nuevo_stock") in (None, "") else []) + \
        ([] if motivo else ["el motivo del ajuste"])
    if faltan:
        return responder(me_falta(faltan))
    valor = entero(g.argumentos.get("nuevo_stock"))
    if valor is None or valor < 0:
        return responder("El nuevo stock debe ser un número entero mayor o igual a cero.")
    if valor == producto.stock_actual:
        return responder(f"El stock de {producto.sku} ya es {valor}; no hay nada que ajustar.")

    pendiente = exigir_confirmacion(
        f"Voy a ajustar el inventario de {producto.sku}, {producto.nombre}: el stock actual es "
        f"{producto.stock_actual} y el nuevo será {valor}, por: {motivo}."
    )
    if pendiente:
        return pendiente

    registrar_movimiento(
        producto, TipoMovimiento.AJUSTE, valor, g.usuario.id,
        motivo=f"{motivo}. {NOTA_ASISTENTE}",
    )
    db.session.commit()
    return responder(f"Listo, el stock de {producto.sku} quedó en {valor}.")


QUE_SE_EDITA = {
    EstadoPedido.PENDIENTE: "la fecha, la sede, la ventana, la prioridad, las observaciones o los productos",
    EstadoPedido.ASIGNADO: "la ventana, la prioridad o las observaciones",
}


def _datos_edicion(pedido):
    """(datos para validar_edicion, productos leidos, error) de lo que se dijo."""
    datos, leidos = {}, None
    if g.argumentos.get("fecha") not in (None, ""):
        fecha = fecha_iso(g.argumentos.get("fecha"), None)
        if fecha is None:
            return None, None, "No entendí la fecha; dímela como año, mes y día."
        datos["fecha_despacho"] = fecha
    if texto("sede"):
        if pedido.cliente is None:
            return None, None, "El pedido no tiene un cliente registrado; su sede no se puede cambiar."
        sede, error = resolver(buscar_sedes(pedido.cliente, texto("sede")), _sede_texto,
                               f"sedes de {pedido.cliente.nombre}",
                               f"No encontré esa sede de {pedido.cliente.nombre}.")
        if sede is None:
            return None, None, error
        datos["sede_id"] = sede.id
    ventana = dichos("ventana_inicio", "ventana_fin")
    if ventana:
        # Si se dijo solo un extremo, el otro se conserva.
        datos["ventana_inicio"] = ventana.get(
            "ventana_inicio", pedido.ventana_inicio.strftime("%H:%M") if pedido.ventana_inicio else "")
        datos["ventana_fin"] = ventana.get(
            "ventana_fin", pedido.ventana_fin.strftime("%H:%M") if pedido.ventana_fin else "")
    if g.argumentos.get("prioridad") not in (None, ""):
        datos["prioridad"] = g.argumentos.get("prioridad")
    if g.argumentos.get("observaciones") is not None:
        datos["observaciones"] = texto("observaciones")
    lineas = g.argumentos.get("productos")
    if isinstance(lineas, list) and lineas:
        datos["items"], leidos, error = _leer_productos(lineas)
        if error:
            return None, None, error
    return datos, leidos, None


def _leer_edicion(pedido, cambios, leidos):
    partes = []
    for campo, (antes, despues) in cambios.items():
        if campo == "fecha_despacho":
            partes.append(f"la fecha del {fecha_larga(antes)} al {fecha_larga(despues)}")
        elif campo == "sede_id":
            nueva = db.session.get(DireccionCliente, despues)
            partes.append(f"la sede a {_sede_texto(nueva)}")
        elif campo in ("ventana_inicio", "ventana_fin"):
            continue
        elif campo == "prioridad":
            partes.append(f"la prioridad de {_prioridad_texto(antes)} a {_prioridad_texto(despues)}")
        elif campo == "observaciones":
            partes.append(f"las observaciones a «{despues}»" if despues else "quitar las observaciones")
        elif campo == "items":
            partes.append(f"los productos a {enumerar(leidos)}")
    if "ventana_inicio" in cambios or "ventana_fin" in cambios:
        inicio = cambios.get("ventana_inicio", (None, pedido.ventana_inicio))[1]
        fin = cambios.get("ventana_fin", (None, pedido.ventana_fin))[1]
        partes.append(f"la ventana a {inicio.strftime('%H:%M')} - {fin.strftime('%H:%M')}"
                      if inicio and fin else "quitar la ventana horaria")
    return enumerar(partes)


@pedidos("editar", "Editar pedido",
         "edita un pedido: PENDIENTE (fecha, sede del mismo cliente, ventana, prioridad, "
         "observaciones y productos) o ASIGNADO (solo ventana, prioridad y observaciones); "
         "requiere pedido")
def editar_pedido_voz():
    pedido, error = _pedido()
    if pedido is None:
        return responder(error)
    if not campos_editables(pedido):
        return responder(f"El pedido {pedido.codigo} está {pedido.estado_etiqueta.lower()} y no se puede "
                         "editar; solo se editan los pendientes y los asignados.")
    datos, leidos, error = _datos_edicion(pedido)
    if error:
        return responder(error)
    if not datos:
        return responder(f"¿Qué quieres cambiar del pedido {pedido.codigo}? Puedo cambiar "
                         f"{QUE_SE_EDITA[pedido.estado]}.")
    try:
        cambios, _ = validar_edicion(pedido, datos)
    except DatosInvalidos as error:
        return responder(frase_datos_invalidos(error))

    aviso = ""
    if pedido.estado == EstadoPedido.ASIGNADO and pedido.ruta is not None:
        aviso = (f" El pedido ya está en la ruta {pedido.ruta.codigo}: después conviene recalcularla "
                 "para que la secuencia tenga en cuenta el cambio.")
    pendiente = exigir_confirmacion(
        f"Voy a cambiar en el pedido {pedido_texto(pedido)} {_leer_edicion(pedido, cambios, leidos)}.{aviso}"
    )
    if pendiente:
        return pendiente

    try:
        editar_pedido(pedido, datos, g.usuario.id, origen=NOTA_ASISTENTE)
    except DatosInvalidos as error:
        db.session.rollback()
        return responder(frase_datos_invalidos(error))
    db.session.commit()
    return responder(f"Listo, actualicé el pedido {pedido.codigo}."
                     + (" ¿Recalculo la ruta?" if aviso else ""))


rutas = funcion_agrupada(
    ESPACIO, "rutas", roles=ROLES, entidad="ruta",
    descripcion="Rutas de reparto. Para el avance de las rutas de hoy usa avance-rutas.",
    parametros={
        "ruta": {"type": "string",
                 "description": "consultar, quitar-parada, recalcular, eliminar: codigo de la ruta o "
                                "nombre de su conductor (su ruta de la fecha)."},
        **PARAMETRO_PEDIDO,
        "conductor": {"type": "string", "description": "crear y agregar-parada: nombre del conductor."},
        "fecha": {"type": "string",
                  "description": "crear: fecha AAAA-MM-DD (por defecto hoy). consultar y demas: la "
                                 "fecha de la ruta del conductor (por defecto hoy)."},
        "vehiculo": {"type": "string", "description": "crear: placa del vehiculo (opcional)."},
        "criterio": {"type": "string", "enum": ["distancia", "ventana"],
                     "description": "crear y recalcular: 'distancia' (por defecto, optimiza la "
                                    "distancia total) o 'ventana' (respeta las ventanas horarias)."},
        "pedidos": {"type": "array", "items": {"type": "string"},
                    "description": "crear: codigos o clientes de los pedidos pendientes de la fecha."},
        "todos_los_pendientes": {"type": "boolean",
                                 "description": "crear: true para incluir todos los pedidos pendientes "
                                                "sin ruta de la fecha."},
    },
)

CRITERIOS = {"distancia": ESTRATEGIA_DISTANCIA, "ventana": ESTRATEGIA_VENTANA}


def _criterio():
    """(estrategia, error). Por defecto, distancia, como en pantalla."""
    dicho = normalizar_texto(texto("criterio"))
    if not dicho:
        return ESTRATEGIA_DISTANCIA, None
    for clave, estrategia in CRITERIOS.items():
        if clave in dicho:
            return estrategia, None
    return None, "El criterio debe ser distancia o ventana horaria."


def _criterio_texto(estrategia):
    return ESTRATEGIAS[estrategia].replace(" (recomendada)", "").lower()


def _fecha_dicha():
    return fecha_iso(g.argumentos.get("fecha"), hoy())


def _ruta():
    buscado = texto("ruta")
    if not buscado:
        return None, me_falta(["la ruta (su código o el nombre del conductor)"])
    fecha = _fecha_dicha()
    if fecha is None:
        return None, "No entendí la fecha; dímela como año, mes y día."
    return resolver(buscar_rutas(buscado, fecha),
                    lambda r: f"{r.codigo} de {r.conductor.nombre}, del {fecha_voz(r.fecha)}", "rutas",
                    f"No encontré una ruta para {buscado}.")


@rutas("consultar", "Consultar ruta",
       "conductor, vehiculo, estado y paradas en orden de una ruta; requiere ruta", modifica=False)
def consultar_ruta():
    ruta, error = _ruta()
    if ruta is None:
        return responder(error)
    paradas = sorted(ruta.pedidos, key=lambda p: p.orden_en_ruta or 0)
    return responder(
        f"La ruta {ruta.codigo} del {fecha_larga(ruta.fecha)} es de {ruta.conductor.nombre}, "
        + (f"con el vehículo {ruta.vehiculo.placa}" if ruta.vehiculo else "sin vehículo")
        + f", y está {ruta.estado_etiqueta.lower()}. "
        + (f"Tiene {cantidad(len(paradas), 'parada')}: "
           + "; ".join(f"{p.orden_en_ruta}, {pedido_texto(p)}, {p.estado_etiqueta.lower()}"
                       for p in paradas[:10])
           + (f"; y {len(paradas) - 10} más" if len(paradas) > 10 else "") + "."
           if paradas else "No tiene paradas.")
    )


@rutas("crear", "Crear ruta",
       "crea una ruta con pedidos pendientes sin ruta de la fecha y calcula su secuencia; requiere "
       "conductor y pedidos (o todos_los_pendientes); fecha, vehiculo y criterio opcionales")
def crear_ruta_voz():
    nombre = texto("conductor")
    lista = g.argumentos.get("pedidos")
    todos = g.argumentos.get("todos_los_pendientes") in (True, "true", "True")
    faltan = ([] if nombre else ["el conductor"]) + \
        ([] if todos or (isinstance(lista, list) and lista) else ["los pedidos"])
    if faltan:
        return responder(me_falta(faltan))
    conductor, error = resolver(buscar_conductores(nombre), lambda c: c.nombre, "conductores",
                                f"No encontré un conductor activo llamado {nombre}.")
    if conductor is None:
        return responder(error)
    fecha = _fecha_dicha()
    if fecha is None:
        return responder("No entendí la fecha; dímela como año, mes y día.")
    estrategia, error = _criterio()
    if error:
        return responder(error)

    vehiculo = None
    if texto("vehiculo"):
        vehiculo, error = resolver(buscar_vehiculos(texto("vehiculo")), lambda v: v.placa, "vehículos",
                                   f"No encontré un vehículo con la placa {texto('vehiculo')}.")
        if vehiculo is None:
            return responder(error)

    sin_ruta = (db.session.query(Pedido)
                .filter(Pedido.estado == EstadoPedido.PENDIENTE, Pedido.ruta_id.is_(None),
                        Pedido.fecha_despacho == fecha)
                .order_by(Pedido.prioridad, Pedido.codigo).all())
    if todos:
        elegidos = sin_ruta
        if not elegidos:
            return responder(f"No hay pedidos pendientes sin ruta para el {fecha_larga(fecha)}.")
    else:
        elegidos = []
        for dicho in lista:
            pedido, error = resolver(buscar_pedidos(str(dicho), fecha=fecha), pedido_texto, "pedidos",
                                     f"No encontré el pedido {dicho}.")
            if pedido is None:
                return responder(error)
            if pedido not in sin_ruta:
                return responder(f"El pedido {pedido_texto(pedido)} no se puede incluir: debe estar "
                                 f"pendiente, sin ruta y para el {fecha_larga(fecha)}.")
            if pedido not in elegidos:
                elegidos.append(pedido)
    datos = {"fecha": fecha, "conductor_id": conductor.id, "vehiculo_id": vehiculo.id if vehiculo else None,
             "estrategia": estrategia, "pedidos": [p.id for p in elegidos]}
    try:
        validar_ruta(datos)
    except DatosInvalidos as error:
        return responder(frase_datos_invalidos(error))

    if vehiculo:
        con_vehiculo = f"con el vehículo {vehiculo.placa}"
    elif conductor.vehiculo is not None:
        con_vehiculo = (f"sin vehículo; {conductor.nombre} tiene asignado el {conductor.vehiculo.placa}, "
                        "dímelo si quieres usarlo")
    else:
        con_vehiculo = "sin vehículo"
    pendiente = exigir_confirmacion(
        f"Voy a crear una ruta para el {fecha_larga(fecha)} con {conductor.nombre}, {con_vehiculo}, "
        f"con {cantidad(len(elegidos), 'parada')}: {enumerar([p.codigo for p in elegidos][:5])}"
        + (f" y {len(elegidos) - 5} más" if len(elegidos) > 5 else "")
        + f"; ordenada para {_criterio_texto(estrategia)}."
    )
    if pendiente:
        return pendiente
    try:
        ruta, resultado = crear_ruta(datos, g.usuario.id, origen=NOTA_ASISTENTE)
    except DatosInvalidos as error:
        db.session.rollback()
        return responder(frase_datos_invalidos(error))
    db.session.commit()
    return responder(
        f"Listo, creé la ruta {ruta.codigo} con {cantidad(len(ruta.pedidos), 'parada')}, "
        f"{valor_leido(float(ruta.distancia_km or 0))} kilómetros estimados."
        + (" " + " ".join(resultado.advertencias) if resultado.advertencias else "")
    )


@rutas("quitar-parada", "Quitar parada de ruta",
       "devuelve un pedido no entregado de la ruta a pendiente sin ruta; requiere ruta y pedido")
def quitar_parada_voz():
    ruta, error = _ruta()
    if ruta is None:
        return responder(error)
    pedido, error = _pedido()
    if pedido is None:
        return responder(error)
    try:
        verificar_quitar_parada(ruta, pedido)
    except PlanificacionInvalida as causa:
        return responder(str(causa))
    pendiente = exigir_confirmacion(
        f"Voy a quitar el pedido {pedido_texto(pedido)}, parada {pedido.orden_en_ruta}, de la ruta "
        f"{ruta.codigo}; vuelve a pendiente sin ruta."
    )
    if pendiente:
        return pendiente
    try:
        quitar_parada(ruta, pedido, g.usuario.id, origen=NOTA_ASISTENTE)
    except PlanificacionInvalida as causa:
        db.session.rollback()
        return responder(str(causa))
    db.session.commit()
    return responder(f"Listo, el pedido {pedido.codigo} volvió a pendiente.")


@rutas("recalcular", "Recalcular ruta",
       "reordena las paradas que siguen abiertas; requiere ruta; criterio opcional")
def recalcular_ruta_voz():
    ruta, error = _ruta()
    if ruta is None:
        return responder(error)
    estrategia, error = _criterio()
    if error:
        return responder(error)
    if ruta.estado == EstadoRuta.FINALIZADA:
        return responder(f"La ruta {ruta.codigo} está finalizada; no se recalcula.")
    abiertas = [p for p in ruta.pedidos if p.estado in EstadoPedido.ABIERTOS]
    if not abiertas:
        return responder(f"La ruta {ruta.codigo} no tiene paradas pendientes por reordenar.")
    pendiente = exigir_confirmacion(
        f"Voy a recalcular la ruta {ruta.codigo} de {ruta.conductor.nombre} para "
        f"{_criterio_texto(estrategia)}: se {'reordena' if len(abiertas) == 1 else 'reordenan'} "
        f"{cantidad(len(abiertas), 'parada pendiente', 'paradas pendientes')}."
    )
    if pendiente:
        return pendiente
    try:
        resultado = recalcular_secuencia(ruta, estrategia)
    except PlanificacionInvalida as causa:
        db.session.rollback()
        return responder(str(causa))
    db.session.commit()
    return responder(
        f"Listo, recalculé la ruta {ruta.codigo}: {valor_leido(float(ruta.distancia_km or 0))} kilómetros "
        "estimados." + (" " + " ".join(resultado.advertencias) if resultado and resultado.advertencias else "")
    )


@rutas("eliminar", "Eliminar ruta",
       "elimina una ruta sin entregas; sus pedidos vuelven a pendiente; requiere ruta")
def eliminar_ruta_voz():
    ruta, error = _ruta()
    if ruta is None:
        return responder(error)
    try:
        verificar_eliminar_ruta(ruta)
    except PlanificacionInvalida as causa:
        return responder(f"No puedo eliminar la ruta {ruta.codigo}. {causa}")
    paradas = len(ruta.pedidos)
    pendiente = exigir_confirmacion(
        f"Voy a eliminar la ruta {ruta.codigo} de {ruta.conductor.nombre}, del {fecha_larga(ruta.fecha)}: "
        + (f"{cantidad(paradas, 'parada vuelve', 'paradas vuelven')} a pendiente."
           if paradas else "no tiene paradas.")
    )
    if pendiente:
        return pendiente
    try:
        codigo, devueltos = eliminar_ruta(ruta, g.usuario.id, origen=NOTA_ASISTENTE)
    except PlanificacionInvalida as causa:
        db.session.rollback()
        return responder(str(causa))
    db.session.commit()
    return responder(f"Listo, eliminé la ruta {codigo}; {cantidad(devueltos, 'pedido volvió', 'pedidos volvieron')} "
                     "a pendiente.")


@rutas("agregar-parada", "Agregar parada a ruta",
       "agrega un pedido pendiente a la ruta de hoy de un conductor y recalcula la secuencia; "
       "no reabre una ruta finalizada; requiere pedido y conductor")
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
                "y no se reabre. Para asignarle más pedidos hoy, puedo crearle una ruta nueva."
            )
        return responder(
            f"{conductor.nombre} no tiene ruta hoy. ¿Quieres que le cree una con este pedido?"
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
