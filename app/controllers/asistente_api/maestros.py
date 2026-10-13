"""Datos maestros por voz: clientes, sedes, productos y vehiculos.

Gestor y admin, igual que en pantalla. Cada entidad es una funcion agrupada
con el parametro `operacion` y usa el mismo servicio que la pantalla
(`clientes`, `inventario`, `vehiculos`), con las mismas reglas: el NIT se
registra una sola vez, el SKU queda fijo con movimientos, la placa no cambia,
y una sede o un vehiculo desactivados no se ofrecen para lo nuevo.

Las ediciones son parciales: solo cambia lo que el usuario dijo, y el resumen
lee cada cambio con su valor anterior y el nuevo.
"""

from flask import g

from app.controllers.asistente_api import (
    cantidad, exigir_confirmacion, funcion_agrupada, responder,
)
from app.controllers.asistente_api.comun import (
    diferencias, dichos, enumerar, frase_datos_invalidos, leer_cambios, me_falta, resolver, texto,
    valor_leido,
)
from app.controllers.asistente_api.gestor import ESPACIO, ROLES, _producto, productos
from app.extensions import db
from app.models import Usuario, Vehiculo, normalizar_texto
from app.services.busqueda_voz import (
    buscar_clientes, buscar_conductores, buscar_sedes, buscar_vehiculos,
)
from app.services.clientes import (
    CAMPOS_CLIENTE, CAMPOS_SEDE, cambiar_estado_cliente, cambiar_estado_sede, crear_cliente,
    crear_sede, editar_cliente, editar_sede, validar_cliente, validar_sede,
)
from app.services.inventario import (
    CAMPOS_PRODUCTO, cambiar_estado_producto, crear_producto, editar_producto, sku_fijo,
    validar_producto, validar_stock_inicial,
)
from app.services.validacion import DatosInvalidos
from app.services.vehiculos import (
    CAMPOS_VEHICULO, cambiar_estado_vehiculo, crear_vehiculo, editar_vehiculo, rutas_abiertas,
    validar_vehiculo,
)


def _no_se_pudo(error):
    db.session.rollback()
    return responder(frase_datos_invalidos(error))


# --------------------------------------------------------------------------
# Clientes
# --------------------------------------------------------------------------

ETIQUETAS_CLIENTE = {"nombre": "el nombre", "documento": "el NIT", "telefono": "el teléfono",
                     "correo": "el correo", "activo": "activo"}

clientes = funcion_agrupada(
    ESPACIO, "clientes", roles=ROLES, entidad="cliente",
    descripcion="Clientes comerciales (empresas destinatarias).",
    parametros={
        "cliente": {"type": "string",
                    "description": "consultar, editar, activar, desactivar: nombre del cliente registrado."},
        "nombre": {"type": "string", "description": "crear: nombre o razon social. editar: el nombre nuevo."},
        "nit": {"type": "string", "description": "crear y editar: NIT, digito por digito (opcional)."},
        "telefono": {"type": "string", "description": "crear y editar: telefono (opcional)."},
        "correo": {"type": "string", "description": "crear y editar: correo (opcional), deletreado."},
    },
)


def _cliente(solo_activos=False):
    buscado = texto("cliente")
    if not buscado:
        return None, me_falta(["el cliente"])
    return resolver(buscar_clientes(buscado, solo_activos=solo_activos), lambda c: c.nombre, "clientes",
                    f"No encontré el cliente {buscado}.")


def _datos_cliente():
    datos = dichos("nombre", "telefono", "correo")
    if g.argumentos.get("nit") not in (None, ""):
        datos["documento"] = str(g.argumentos["nit"]).strip()
    return datos


def _texto_sede(sede):
    return f"{sede.etiqueta}, en {sede.direccion}" + ("" if sede.activa else " (desactivada)")


@clientes("consultar", "Consultar cliente",
          "datos del cliente: NIT, telefono, correo, estado, sedes y acceso al portal; requiere cliente",
          modifica=False)
def consultar_cliente():
    cliente, error = _cliente()
    if cliente is None:
        return responder(error)
    sedes = list(cliente.direcciones)
    return responder(
        f"{cliente.nombre}: NIT {cliente.documento or 'sin registrar'}, teléfono "
        f"{cliente.telefono or 'sin dato'}, correo {cliente.correo or 'sin dato'}. "
        f"Está {'activo' if cliente.activo else 'inactivo'}"
        f"{' y tiene acceso al portal' if cliente.usuario_id else ', sin acceso al portal'}. "
        + (f"Tiene {cantidad(len(sedes), 'sede')}: {enumerar([_texto_sede(s) for s in sedes])}."
           if sedes else "No tiene sedes registradas.")
    )


@clientes("crear", "Crear cliente",
          "registra un cliente; requiere nombre; NIT, telefono y correo opcionales")
def crear_cliente_voz():
    datos = _datos_cliente()
    if "nombre" not in datos:
        return responder(me_falta(["el nombre del cliente"]))
    try:
        limpios = validar_cliente({**datos, "activo": True})
    except DatosInvalidos as error:
        return responder(frase_datos_invalidos(error))

    pendiente = exigir_confirmacion(
        f"Voy a registrar el cliente {limpios['nombre']}, NIT {valor_leido(limpios['documento'])}, "
        f"teléfono {valor_leido(limpios['telefono'])} y correo {valor_leido(limpios['correo'])}."
        + (" El NIT queda fijo una vez registrado." if limpios["documento"] else "")
        + " Después podrás agregarle sus sedes."
    )
    if pendiente:
        return pendiente
    try:
        cliente = crear_cliente({**datos, "activo": True})
    except DatosInvalidos as error:
        return _no_se_pudo(error)
    db.session.commit()
    return responder(f"Listo, registré el cliente {cliente.nombre}. ¿Le agrego una sede?")


@clientes("editar", "Editar cliente",
          "cambia nombre, telefono o correo; el NIT solo se registra si no tenia; requiere cliente")
def editar_cliente_voz():
    cliente, error = _cliente()
    if cliente is None:
        return responder(error)
    datos = _datos_cliente()
    if not datos:
        return responder(f"¿Qué quieres cambiar de {cliente.nombre}? Puedo cambiar el nombre, el "
                         "teléfono y el correo, o registrar el NIT si no lo tiene.")
    actuales = {campo: getattr(cliente, campo) for campo in CAMPOS_CLIENTE}
    try:
        limpios = validar_cliente(datos, cliente)
    except DatosInvalidos as error:
        return responder(frase_datos_invalidos(error))
    cambios = diferencias(actuales, limpios, CAMPOS_CLIENTE)
    if not cambios:
        return responder(f"{cliente.nombre} ya tiene esos datos; no hay nada que cambiar.")

    pendiente = exigir_confirmacion(
        f"Voy a cambiar en el cliente {cliente.nombre} {leer_cambios(cambios, ETIQUETAS_CLIENTE)}."
        + (" El NIT queda fijo una vez registrado." if "documento" in cambios else "")
    )
    if pendiente:
        return pendiente
    try:
        editar_cliente(cliente, datos)
    except DatosInvalidos as error:
        return _no_se_pudo(error)
    db.session.commit()
    return responder(f"Listo, actualicé el cliente {cliente.nombre}.")


def _estado_cliente(activo):
    cliente, error = _cliente()
    if cliente is None:
        return responder(error)
    if cliente.activo == activo:
        return responder(f"El cliente {cliente.nombre} ya está {'activo' if activo else 'inactivo'}.")
    pendiente = exigir_confirmacion(
        f"Voy a {'activar' if activo else 'desactivar'} el cliente {cliente.nombre}."
        + ("" if activo else " No se le podrán crear pedidos nuevos por voz; los ya creados siguen igual.")
    )
    if pendiente:
        return pendiente
    try:
        cambiar_estado_cliente(cliente, activo)
    except DatosInvalidos as error:
        return _no_se_pudo(error)
    db.session.commit()
    return responder(f"Listo, el cliente {cliente.nombre} quedó {'activo' if activo else 'inactivo'}.")


@clientes("activar", "Activar cliente", "reactiva un cliente; requiere cliente")
def activar_cliente():
    return _estado_cliente(True)


@clientes("desactivar", "Desactivar cliente", "desactiva un cliente; requiere cliente")
def desactivar_cliente():
    return _estado_cliente(False)


# --------------------------------------------------------------------------
# Sedes
# --------------------------------------------------------------------------

ETIQUETAS_SEDE = {"etiqueta": "el nombre de la sede", "direccion": "la dirección", "ciudad": "la ciudad",
                  "latitud": "la latitud", "longitud": "la longitud",
                  "ventana_inicio": "el inicio de la ventana", "ventana_fin": "el fin de la ventana"}

sedes = funcion_agrupada(
    ESPACIO, "sedes", roles=ROLES, entidad="sede",
    descripcion="Sedes (puntos de entrega) de un cliente. Una sede desactivada no se ofrece para "
                "pedidos nuevos.",
    parametros={
        "cliente": {"type": "string", "description": "Nombre del cliente. Requerido en todas."},
        "sede": {"type": "string",
                 "description": "editar, activar, desactivar: nombre o direccion de la sede."},
        "etiqueta": {"type": "string",
                     "description": "crear y editar: nombre de la sede (\"Bodega norte\"); opcional."},
        "direccion": {"type": "string", "description": "crear (requerida) y editar: direccion."},
        "ciudad": {"type": "string", "description": "crear y editar: ciudad; por defecto Bogota."},
        "latitud": {"type": "number", "description": "crear y editar: latitud (junto con la longitud)."},
        "longitud": {"type": "number", "description": "crear y editar: longitud (junto con la latitud)."},
        "ventana_inicio": {"type": "string", "description": "crear y editar: inicio de la ventana, HH:MM."},
        "ventana_fin": {"type": "string", "description": "crear y editar: fin de la ventana, HH:MM."},
    },
)


def _leer_sede(limpios):
    ventana = (f"ventana de {valor_leido(limpios['ventana_inicio'])} a {valor_leido(limpios['ventana_fin'])}"
               if limpios["ventana_inicio"] else "sin ventana horaria")
    coordenadas = "con coordenadas" if limpios["latitud"] is not None else "sin coordenadas"
    return f"{limpios['direccion']}, {limpios['ciudad']}; {ventana}, {coordenadas}"


def _sede_de(cliente):
    buscado = texto("sede")
    if not buscado:
        return None, me_falta(["la sede"])
    return resolver(buscar_sedes(cliente, buscado, solo_activas=False), _texto_sede,
                    f"sedes de {cliente.nombre}", f"No encontré esa sede de {cliente.nombre}.")


@sedes("consultar", "Consultar sedes", "lista las sedes del cliente; requiere cliente", modifica=False)
def consultar_sedes():
    cliente, error = _cliente()
    if cliente is None:
        return responder(error)
    lista = list(cliente.direcciones)
    if not lista:
        return responder(f"{cliente.nombre} no tiene sedes registradas.")
    return responder(f"{cliente.nombre} tiene {cantidad(len(lista), 'sede')}: "
                     + "; ".join(f"{s.etiqueta}: " + _leer_sede({c: getattr(s, c) for c in CAMPOS_SEDE})
                                 + ("" if s.activa else ", desactivada") for s in lista) + ".")


@sedes("crear", "Agregar sede",
       "agrega una sede al cliente; requiere cliente y direccion; ciudad, ventana y coordenadas opcionales")
def crear_sede_voz():
    cliente, error = _cliente()
    if cliente is None:
        return responder(error)
    datos = dichos(*CAMPOS_SEDE)
    if "direccion" not in datos:
        return responder(me_falta(["la dirección de la sede"]))
    try:
        limpios = validar_sede(cliente, datos)
    except DatosInvalidos as error:
        return responder(frase_datos_invalidos(error))

    pendiente = exigir_confirmacion(
        f"Voy a agregar a {cliente.nombre} la sede {limpios['etiqueta']}: {_leer_sede(limpios)}."
    )
    if pendiente:
        return pendiente
    try:
        sede = crear_sede(cliente, datos)
    except DatosInvalidos as error:
        return _no_se_pudo(error)
    db.session.commit()
    return responder(f"Listo, agregué la sede {sede.etiqueta} a {cliente.nombre}.")


@sedes("editar", "Editar sede",
       "cambia nombre, direccion, ciudad, coordenadas o ventana; requiere cliente y sede")
def editar_sede_voz():
    cliente, error = _cliente()
    if cliente is None:
        return responder(error)
    sede, error = _sede_de(cliente)
    if sede is None:
        return responder(error)
    datos = dichos(*CAMPOS_SEDE)
    if not datos:
        return responder(f"¿Qué quieres cambiar de la sede {sede.etiqueta}? Puedo cambiar su nombre, "
                         "dirección, ciudad, coordenadas o ventana horaria.")
    # Una ventana se cambia completa: si se dijo solo un extremo, el otro se conserva.
    if ("ventana_inicio" in datos) != ("ventana_fin" in datos):
        datos.setdefault("ventana_inicio", valor_leido(sede.ventana_inicio) if sede.ventana_inicio else "")
        datos.setdefault("ventana_fin", valor_leido(sede.ventana_fin) if sede.ventana_fin else "")
    actuales = {campo: getattr(sede, campo) for campo in CAMPOS_SEDE}
    try:
        limpios = validar_sede(cliente, datos, sede)
    except DatosInvalidos as error:
        return responder(frase_datos_invalidos(error))
    cambios = diferencias(actuales, limpios, CAMPOS_SEDE)
    if not cambios:
        return responder(f"La sede {sede.etiqueta} ya tiene esos datos; no hay nada que cambiar.")

    pendiente = exigir_confirmacion(
        f"Voy a cambiar en la sede {sede.etiqueta} de {cliente.nombre} "
        f"{leer_cambios(cambios, ETIQUETAS_SEDE)}. Los pedidos ya creados conservan su dirección."
    )
    if pendiente:
        return pendiente
    try:
        editar_sede(sede, datos)
    except DatosInvalidos as error:
        return _no_se_pudo(error)
    db.session.commit()
    return responder(f"Listo, actualicé la sede {sede.etiqueta} de {cliente.nombre}.")


def _estado_sede(activa):
    cliente, error = _cliente()
    if cliente is None:
        return responder(error)
    sede, error = _sede_de(cliente)
    if sede is None:
        return responder(error)
    if sede.activa == activa:
        return responder(f"La sede {sede.etiqueta} ya está {'activa' if activa else 'desactivada'}.")
    pendiente = exigir_confirmacion(
        f"Voy a {'activar' if activa else 'desactivar'} la sede {sede.etiqueta} de {cliente.nombre}"
        + (". Ya no se ofrecerá para pedidos nuevos; los ya creados siguen igual." if not activa else ".")
    )
    if pendiente:
        return pendiente
    try:
        cambiar_estado_sede(sede, activa)
    except DatosInvalidos as error:
        return _no_se_pudo(error)
    db.session.commit()
    return responder(f"Listo, la sede {sede.etiqueta} quedó {'activa' if activa else 'desactivada'}.")


@sedes("activar", "Activar sede", "reactiva una sede; requiere cliente y sede")
def activar_sede():
    return _estado_sede(True)


@sedes("desactivar", "Desactivar sede",
       "desactiva una sede: no se ofrece para pedidos nuevos; requiere cliente y sede")
def desactivar_sede():
    return _estado_sede(False)


# --------------------------------------------------------------------------
# Productos (alta, edicion y estado; el stock esta en gestor.py)
# --------------------------------------------------------------------------

ETIQUETAS_PRODUCTO = {"sku": "el SKU", "nombre": "el nombre", "descripcion": "la descripción",
                      "unidad": "la unidad", "stock_minimo": "el stock mínimo", "activo": "activo"}
CAMPOS_EDITABLES_PRODUCTO = ("sku", "nombre", "descripcion", "unidad", "stock_minimo")

# La funcion `productos` se declara en gestor.py (entrada y ajuste); aqui se
# suman sus operaciones de catalogo y los parametros que usan.
productos.agregar_parametros({
    "sku": {"type": "string", "description": "crear y editar: SKU del producto, letra por letra."},
    "nombre": {"type": "string", "description": "crear y editar: nombre del producto."},
    "descripcion": {"type": "string", "description": "crear y editar: descripcion (opcional)."},
    "unidad": {"type": "string", "description": "crear y editar: unidad de medida; por defecto UND."},
    "stock_minimo": {"type": "integer",
                     "description": "crear y editar: stock minimo, entero mayor o igual a cero."},
    "stock_inicial": {"type": "integer",
                      "description": "crear: unidades con las que entra (opcional; entra como entrada)."},
})


@productos("crear", "Crear producto",
           "crea un producto; requiere SKU y nombre; con stock inicial, el SKU queda fijo")
def crear_producto_voz():
    datos = dichos(*CAMPOS_EDITABLES_PRODUCTO, "stock_inicial")
    faltan = [etiqueta for campo, etiqueta in (("sku", "el SKU"), ("nombre", "el nombre del producto"))
              if campo not in datos]
    if faltan:
        return responder(me_falta(faltan))
    try:
        limpios = validar_producto({**datos, "activo": True})
        stock_inicial = validar_stock_inicial(datos)
    except DatosInvalidos as error:
        return responder(frase_datos_invalidos(error))

    pendiente = exigir_confirmacion(
        f"Voy a crear el producto {limpios['sku']}, {limpios['nombre']}, en {limpios['unidad']}, con stock "
        f"mínimo {limpios['stock_minimo']}"
        + (f" y un stock inicial de {stock_inicial}, que entra como entrada de mercancía. Con stock "
           "inicial, el SKU queda fijo." if stock_inicial else " y sin stock inicial.")
    )
    if pendiente:
        return pendiente
    try:
        producto = crear_producto({**datos, "activo": True}, g.usuario.id,
                                  motivo_stock="Stock inicial. Registrado por el asistente de voz")
    except DatosInvalidos as error:
        return _no_se_pudo(error)
    db.session.commit()
    return responder(f"Listo, creé el producto {producto.sku}.")


@productos("editar", "Editar producto",
           "cambia SKU (solo si no tiene movimientos), nombre, descripcion, unidad o stock minimo; "
           "no cambia el stock; requiere producto")
def editar_producto_voz():
    producto, error = _producto(solo_activos=False)
    if producto is None:
        return responder(error)
    datos = dichos(*CAMPOS_EDITABLES_PRODUCTO)
    if not datos:
        return responder(f"¿Qué quieres cambiar de {producto.sku}? Puedo cambiar el nombre, la descripción, "
                         "la unidad o el stock mínimo"
                         + ("." if sku_fijo(producto) else ", o el SKU porque aún no tiene movimientos.")
                         + " El stock cambia con una entrada o un ajuste.")
    actuales = {campo: getattr(producto, campo) for campo in CAMPOS_PRODUCTO}
    try:
        limpios = validar_producto(datos, producto)
    except DatosInvalidos as error:
        return responder(frase_datos_invalidos(error))
    cambios = diferencias(actuales, limpios, CAMPOS_EDITABLES_PRODUCTO)
    if not cambios:
        return responder(f"{producto.sku} ya tiene esos datos; no hay nada que cambiar.")

    pendiente = exigir_confirmacion(
        f"Voy a cambiar en el producto {producto.sku} {leer_cambios(cambios, ETIQUETAS_PRODUCTO)}."
    )
    if pendiente:
        return pendiente
    try:
        editar_producto(producto, datos)
    except DatosInvalidos as error:
        return _no_se_pudo(error)
    db.session.commit()
    return responder(f"Listo, actualicé el producto {producto.sku}.")


def _estado_producto(activo):
    producto, error = _producto(solo_activos=False)
    if producto is None:
        return responder(error)
    if producto.activo == activo:
        return responder(f"El producto {producto.sku} ya está {'activo' if activo else 'inactivo'}.")
    pendiente = exigir_confirmacion(
        f"Voy a {'activar' if activo else 'desactivar'} el producto {producto.sku}, {producto.nombre}"
        + (". No se ofrecerá para pedidos nuevos ni generará avisos de stock bajo." if not activo else ".")
    )
    if pendiente:
        return pendiente
    try:
        cambiar_estado_producto(producto, activo)
    except DatosInvalidos as error:
        return _no_se_pudo(error)
    db.session.commit()
    return responder(f"Listo, el producto {producto.sku} quedó {'activo' if activo else 'inactivo'}.")


@productos("activar", "Activar producto", "reactiva un producto; requiere producto")
def activar_producto():
    return _estado_producto(True)


@productos("desactivar", "Desactivar producto", "desactiva un producto; requiere producto")
def desactivar_producto():
    return _estado_producto(False)


# --------------------------------------------------------------------------
# Vehiculos
# --------------------------------------------------------------------------

ETIQUETAS_VEHICULO = {"tipo": "el tipo", "capacidad_kg": "la capacidad en kilos",
                      "capacidad_unidades": "la capacidad en unidades", "conductor_id": "el conductor",
                      "activo": "activo"}
SIN_CONDUCTOR = {"ninguno", "nadie", "sin conductor", "ningun conductor", "quitar", "quitarlo", "sin"}

vehiculos = funcion_agrupada(
    ESPACIO, "vehiculos", roles=ROLES, entidad="vehiculo",
    descripcion="Vehiculos de reparto. La placa no cambia despues de crearlo.",
    parametros={
        "placa": {"type": "string",
                  "description": "Placa, letra por letra. Requerida salvo en consultar (sin placa, lista todos)."},
        "tipo": {"type": "string", "description": "crear y editar: tipo (Furgon, Camion...); por defecto Furgon."},
        "capacidad_kg": {"type": "number", "description": "crear y editar: capacidad en kilos."},
        "capacidad_unidades": {"type": "integer", "description": "crear y editar: capacidad en unidades."},
        "conductor": {"type": "string",
                      "description": "crear y editar: nombre del conductor asignado, o 'ninguno' para quitarlo."},
    },
)


def _vehiculo():
    buscado = texto("placa")
    if not buscado:
        return None, me_falta(["la placa del vehículo"])
    return resolver(buscar_vehiculos(buscado), lambda v: v.placa, "vehículos",
                    f"No encontré un vehículo con la placa {buscado}.")


def _nombre_conductor(conductor_id):
    conductor = db.session.get(Usuario, conductor_id) if conductor_id else None
    return conductor.nombre if conductor else None


def _datos_vehiculo():
    """(datos para el servicio, mensaje de error o None)."""
    datos = dichos("tipo", "capacidad_kg", "capacidad_unidades")
    dicho = texto("conductor")
    if dicho:
        if normalizar_texto(dicho) in SIN_CONDUCTOR:
            datos["conductor_id"] = 0
        else:
            conductor, error = resolver(buscar_conductores(dicho), lambda c: c.nombre, "conductores",
                                        f"No encontré un conductor activo llamado {dicho}.")
            if conductor is None:
                return None, error
            datos["conductor_id"] = conductor.id
    return datos, None


def _leer_vehiculo(vehiculo_o_datos, conductor_id):
    d = vehiculo_o_datos
    return (f"{d['tipo']}, {valor_leido(float(d['capacidad_kg'] or 0))} kilos y "
            f"{d['capacidad_unidades']} unidades, "
            + (f"con {_nombre_conductor(conductor_id)} como conductor" if conductor_id else "sin conductor"))


@vehiculos("consultar", "Consultar vehículos",
           "datos de un vehiculo por su placa, o la lista de vehiculos si no se dice placa", modifica=False)
def consultar_vehiculos():
    def describir_vehiculo(v):
        abiertas = rutas_abiertas(v)
        return (f"{v.placa}: " + _leer_vehiculo({c: getattr(v, c) for c in CAMPOS_VEHICULO}, v.conductor_id)
                + ("" if v.activo else ", inactivo")
                + (f", en {cantidad(abiertas, 'ruta abierta', 'rutas abiertas')}" if abiertas else ""))

    if not texto("placa"):
        todos = db.session.query(Vehiculo).order_by(Vehiculo.placa).all()
        if not todos:
            return responder("No hay vehículos registrados.")
        return responder(f"Hay {cantidad(len(todos), 'vehículo')}: "
                         + "; ".join(describir_vehiculo(v) for v in todos) + ".")
    vehiculo, error = _vehiculo()
    if vehiculo is None:
        return responder(error)
    return responder(describir_vehiculo(vehiculo) + ".")


@vehiculos("crear", "Crear vehículo",
           "registra un vehiculo; requiere placa; tipo, capacidades y conductor opcionales")
def crear_vehiculo_voz():
    placa = texto("placa")
    if not placa:
        return responder(me_falta(["la placa del vehículo"]))
    datos, error = _datos_vehiculo()
    if datos is None:
        return responder(error)
    datos = {**datos, "placa": placa, "activo": True}
    try:
        limpios = validar_vehiculo(datos)
    except DatosInvalidos as error:
        return responder(frase_datos_invalidos(error))

    pendiente = exigir_confirmacion(
        f"Voy a registrar el vehículo de placa {limpios['placa']}: "
        f"{_leer_vehiculo(limpios, limpios['conductor_id'])}. La placa no se podrá cambiar."
    )
    if pendiente:
        return pendiente
    try:
        vehiculo = crear_vehiculo(datos)
    except DatosInvalidos as error:
        return _no_se_pudo(error)
    db.session.commit()
    return responder(f"Listo, registré el vehículo {vehiculo.placa}.")


@vehiculos("editar", "Editar vehículo",
           "cambia tipo, capacidades o conductor asignado; la placa no cambia; requiere placa")
def editar_vehiculo_voz():
    vehiculo, error = _vehiculo()
    if vehiculo is None:
        return responder(error)
    datos, error = _datos_vehiculo()
    if datos is None:
        return responder(error)
    if not datos:
        return responder(f"¿Qué quieres cambiar del vehículo {vehiculo.placa}? Puedo cambiar el tipo, las "
                         "capacidades o el conductor asignado. La placa no cambia.")
    actuales = {campo: getattr(vehiculo, campo) for campo in CAMPOS_VEHICULO}
    try:
        limpios = validar_vehiculo(datos, vehiculo)
    except DatosInvalidos as error:
        return responder(frase_datos_invalidos(error))
    cambios = diferencias(actuales, limpios, ("tipo", "capacidad_kg", "capacidad_unidades", "conductor_id"))
    if not cambios:
        return responder(f"El vehículo {vehiculo.placa} ya tiene esos datos; no hay nada que cambiar.")
    leidos = dict(cambios)
    if "conductor_id" in leidos:
        antes, despues = leidos.pop("conductor_id")
        leidos["conductor_id"] = (_nombre_conductor(antes), _nombre_conductor(despues))

    pendiente = exigir_confirmacion(
        f"Voy a cambiar en el vehículo {vehiculo.placa} {leer_cambios(leidos, ETIQUETAS_VEHICULO)}."
    )
    if pendiente:
        return pendiente
    try:
        editar_vehiculo(vehiculo, datos)
    except DatosInvalidos as error:
        return _no_se_pudo(error)
    db.session.commit()
    return responder(f"Listo, actualicé el vehículo {vehiculo.placa}.")


def _estado_vehiculo(activo):
    vehiculo, error = _vehiculo()
    if vehiculo is None:
        return responder(error)
    if vehiculo.activo == activo:
        return responder(f"El vehículo {vehiculo.placa} ya está {'activo' if activo else 'inactivo'}.")
    if not activo and rutas_abiertas(vehiculo):
        return responder(f"No puedo desactivar el vehículo {vehiculo.placa}: está en una ruta abierta.")
    pendiente = exigir_confirmacion(
        f"Voy a {'activar' if activo else 'desactivar'} el vehículo {vehiculo.placa}"
        + (". No se ofrecerá para rutas nuevas." if not activo else ".")
    )
    if pendiente:
        return pendiente
    try:
        cambiar_estado_vehiculo(vehiculo, activo)
    except DatosInvalidos as error:
        return _no_se_pudo(error)
    db.session.commit()
    return responder(f"Listo, el vehículo {vehiculo.placa} quedó {'activo' if activo else 'inactivo'}.")


@vehiculos("activar", "Activar vehículo", "reactiva un vehiculo; requiere placa")
def activar_vehiculo():
    return _estado_vehiculo(True)


@vehiculos("desactivar", "Desactivar vehículo",
           "desactiva un vehiculo; no si esta en una ruta abierta; requiere placa")
def desactivar_vehiculo():
    return _estado_vehiculo(False)
