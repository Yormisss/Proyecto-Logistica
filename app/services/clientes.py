"""Resolucion de clientes y puntos de entrega.

Punto unico por el que pasan tanto la carga manual (RF2) como la importacion
CSV: recibe los datos sueltos del destinatario y devuelve las entidades
`Cliente` / `DireccionCliente` correspondientes, creandolas solo si no existen.

Sin esta capa cada archivo del ERP volveria a inventar el mismo cliente con una
escritura distinta, que es justamente la redundancia que se esta eliminando.
"""

from app.extensions import db
from app.models import Cliente, DireccionCliente, normalizar_texto

CIUDAD_POR_DEFECTO = "Bogota"


class Resolutor:
    """Cachea los clientes y sedes tocados durante una misma carga.

    Una importacion con 200 filas del mismo cliente no debe producir 200
    consultas ni 200 clientes: el cache resuelve las repeticiones dentro del
    lote antes de llegar a la base.
    """

    def __init__(self):
        self._por_documento = {}
        self._por_nombre = {}

    # --- Clientes ---------------------------------------------------------
    def cliente(self, nombre, documento=None, telefono=None, correo=None):
        """Devuelve el cliente que corresponde a estos datos, creandolo si falta.

        La clave natural es el documento (NIT o cedula) cuando el ERP lo exporta;
        si no viene, se usa el nombre normalizado. Se prefiere el documento porque
        una razon social puede reescribirse, pero el NIT no.
        """
        nombre = (nombre or "").strip()
        if not nombre:
            raise ValueError("El nombre del cliente es obligatorio")

        documento = (documento or "").strip() or None
        clave_nombre = normalizar_texto(nombre)

        cliente = None
        if documento:
            cliente = self._por_documento.get(documento)
            if cliente is None:
                cliente = (
                    db.session.query(Cliente).filter_by(documento=documento).first()
                )
        if cliente is None:
            cliente = self._por_nombre.get(clave_nombre)
            if cliente is None:
                cliente = (
                    db.session.query(Cliente)
                    .filter_by(nombre_normalizado=clave_nombre)
                    .first()
                )
            # Un cliente encontrado por nombre pero con otro documento es otra
            # empresa homonima: no se reutiliza.
            if cliente is not None and documento and cliente.documento not in (None, documento):
                cliente = None

        if cliente is None:
            cliente = Cliente(
                nombre=nombre,
                documento=documento,
                telefono=telefono or None,
                correo=correo or None,
            )
            db.session.add(cliente)
            db.session.flush()   # Necesario para disponer del id en el pedido.
        else:
            # Completa datos que el registro original no traia, sin sobrescribir
            # lo que el area comercial ya haya corregido a mano.
            if documento and not cliente.documento:
                cliente.documento = documento
            if telefono and not cliente.telefono:
                cliente.telefono = telefono
            if correo and not cliente.correo:
                cliente.correo = correo

        if cliente.documento:
            self._por_documento[cliente.documento] = cliente
        self._por_nombre[cliente.nombre_normalizado] = cliente
        return cliente

    # --- Direcciones ------------------------------------------------------
    def direccion(self, cliente, direccion, ciudad=None, latitud=None, longitud=None,
                  ventana_inicio=None, ventana_fin=None):
        """Devuelve la sede del cliente que coincide con esta direccion."""
        direccion = (direccion or "").strip()
        if not direccion:
            raise ValueError("La direccion de entrega es obligatoria")

        ciudad = (ciudad or "").strip() or CIUDAD_POR_DEFECTO
        clave = normalizar_texto(direccion)

        existente = next(
            (
                d for d in cliente.direcciones
                if d.direccion_normalizada == clave
                and normalizar_texto(d.ciudad) == normalizar_texto(ciudad)
            ),
            None,
        )

        if existente is not None:
            # Si la sede se registro sin geolocalizar y ahora llegan coordenadas,
            # se aprovechan: dejan de recapturarse en cada pedido.
            if existente.latitud is None and latitud is not None:
                existente.latitud = latitud
                existente.longitud = longitud
            if existente.ventana_inicio is None and ventana_inicio is not None:
                existente.ventana_inicio = ventana_inicio
                existente.ventana_fin = ventana_fin
            return existente

        nueva = DireccionCliente(
            cliente_id=cliente.id,
            etiqueta="Principal" if not cliente.direcciones else f"Sede {len(cliente.direcciones) + 1}",
            direccion=direccion,
            ciudad=ciudad,
            latitud=latitud,
            longitud=longitud,
            ventana_inicio=ventana_inicio,
            ventana_fin=ventana_fin,
        )
        cliente.direcciones.append(nueva)
        db.session.add(nueva)
        db.session.flush()
        return nueva


def vincular_destino(pedido, cliente, direccion):
    """Asocia el pedido a su cliente y copia el snapshot historico del destino.

    La copia es deliberada: ver el comentario de los campos de entrega en
    `app.models.pedido.Pedido`.
    """
    pedido.cliente_id = cliente.id
    pedido.direccion_id = direccion.id

    pedido.cliente_nombre = cliente.nombre
    pedido.cliente_telefono = pedido.cliente_telefono or cliente.telefono
    pedido.direccion = direccion.direccion
    pedido.ciudad = direccion.ciudad
    if pedido.latitud is None:
        pedido.latitud = direccion.latitud
        pedido.longitud = direccion.longitud
    if pedido.ventana_inicio is None and pedido.ventana_fin is None:
        pedido.ventana_inicio = direccion.ventana_inicio
        pedido.ventana_fin = direccion.ventana_fin
    return pedido


def resolver_destino(datos, resolutor=None):
    """Atajo para una sola orden: devuelve (cliente, direccion).

    `datos` acepta las claves cliente_nombre, cliente_documento, cliente_telefono,
    cliente_correo, direccion, ciudad, latitud, longitud, ventana_inicio y
    ventana_fin.
    """
    resolutor = resolutor or Resolutor()
    cliente = resolutor.cliente(
        datos.get("cliente_nombre"),
        documento=datos.get("cliente_documento"),
        telefono=datos.get("cliente_telefono"),
        correo=datos.get("cliente_correo"),
    )
    direccion = resolutor.direccion(
        cliente,
        datos.get("direccion"),
        ciudad=datos.get("ciudad"),
        latitud=datos.get("latitud"),
        longitud=datos.get("longitud"),
        ventana_inicio=datos.get("ventana_inicio"),
        ventana_fin=datos.get("ventana_fin"),
    )
    return cliente, direccion


def existe_cliente(nombre, documento=None):
    """Indica si el cliente ya esta registrado. Usado por la vista previa del CSV."""
    documento = (documento or "").strip() or None
    if documento:
        if db.session.query(Cliente.id).filter_by(documento=documento).first():
            return True
    clave = normalizar_texto(nombre)
    if not clave:
        return False
    return db.session.query(Cliente.id).filter_by(nombre_normalizado=clave).first() is not None


# --------------------------------------------------------------------------
# Alta y edicion de clientes y sedes (pantalla y asistente de voz)
# --------------------------------------------------------------------------
# Las ediciones son parciales: solo cambian los campos que vienen en `datos`,
# de modo que sirven igual al formulario completo de la pantalla y al dialogo
# por voz, que va recibiendo los datos de a uno. Ninguna funcion hace commit.

from app.services.validacion import DatosInvalidos, Validador  # noqa: E402

CAMPOS_CLIENTE = ("nombre", "documento", "telefono", "correo", "activo")
CAMPOS_SEDE = ("etiqueta", "direccion", "ciudad", "latitud", "longitud",
               "ventana_inicio", "ventana_fin")


def _booleano(valor, defecto=True):
    if valor is None or valor == "":
        return defecto
    if isinstance(valor, str):
        return valor.strip().lower() in ("1", "true", "si", "sí", "on", "y", "yes")
    return bool(valor)


def _actuales(entidad, campos):
    return {campo: getattr(entidad, campo) for campo in campos} if entidad is not None else {}


def _cambios(antes, despues):
    """{campo: (antes, despues)} de lo que cambio, para la bitacora y la auditoria."""
    return {campo: (antes.get(campo), valor) for campo, valor in despues.items()
            if antes.get(campo) != valor}


def validar_cliente(datos, cliente=None):
    """Datos normalizados de un cliente; lanza DatosInvalidos si no cumplen.

    El NIT es unico y, una vez registrado, queda fijo: si el cliente se creo
    sin NIT se puede registrar despues, una sola vez.
    """
    datos = {**_actuales(cliente, CAMPOS_CLIENTE), **datos}
    v = Validador()
    nombre = v.texto(datos, "nombre", "el nombre del cliente", 160, requerido=True)
    documento = v.texto(datos, "documento", "el NIT", 30)
    if cliente is not None and cliente.documento and documento != cliente.documento:
        v.invalido("documento", "El NIT queda fijo una vez registrado.")
        documento = cliente.documento
    telefono = v.texto(datos, "telefono", "el teléfono", 30)
    correo = v.correo(datos, "correo", "el correo")
    activo = _booleano(datos.get("activo"))

    propio = cliente.id if cliente is not None else None
    if documento:
        choque = db.session.query(Cliente).filter(Cliente.documento == documento)
        if propio:
            choque = choque.filter(Cliente.id != propio)
        if choque.first():
            v.invalido("documento", "Ya existe un cliente con este NIT.")
    if nombre:
        # Un homonimo con NIT distinto es legitimo, pero casi siempre es un
        # duplicado por error de escritura: se bloquea si no hay NIT que los separe.
        choque = db.session.query(Cliente).filter(Cliente.nombre_normalizado == normalizar_texto(nombre))
        if propio:
            choque = choque.filter(Cliente.id != propio)
        gemelo = choque.first()
        if gemelo is not None and not (documento and gemelo.documento):
            v.invalido("nombre", f"'{gemelo.nombre}' ya existe y no se distingue de este nombre. "
                                 "Indique el NIT de ambos si son empresas distintas.")
    v.lanzar_si_hay()
    return {"nombre": nombre, "documento": documento, "telefono": telefono, "correo": correo,
            "activo": activo}


def crear_cliente(datos):
    limpios = validar_cliente(datos)
    cliente = Cliente(**limpios)
    db.session.add(cliente)
    db.session.flush()
    return cliente


def editar_cliente(cliente, datos):
    """Aplica los cambios y devuelve {campo: (antes, despues)}."""
    antes = _actuales(cliente, CAMPOS_CLIENTE)
    limpios = validar_cliente(datos, cliente)
    cambios = _cambios(antes, limpios)
    if "nombre" in cambios:
        # `renombrar` mantiene sincronizada la clave de deduplicacion.
        cliente.renombrar(limpios["nombre"])
    for campo in ("documento", "telefono", "correo", "activo"):
        setattr(cliente, campo, limpios[campo])
    return cambios


def cambiar_estado_cliente(cliente, activo):
    if cliente.activo == activo:
        raise DatosInvalidos([(None, f"El cliente {cliente.nombre} ya está "
                                     f"{'activo' if activo else 'inactivo'}.")])
    cliente.activo = activo


def validar_sede(cliente, datos, sede=None):
    """Datos normalizados de una sede del cliente; lanza DatosInvalidos si no cumplen.

    Coordenadas opcionales (las dos o ninguna, dentro de su rango) y ventana
    horaria opcional (las dos horas, inicio antes del fin). La direccion no
    puede repetirse en la misma ciudad entre las sedes del cliente.
    """
    datos = {**_actuales(sede, CAMPOS_SEDE), **datos}
    v = Validador()
    etiqueta = v.texto(datos, "etiqueta", "la etiqueta", 80)
    direccion = v.texto(datos, "direccion", "la dirección", 255, requerido=True)
    ciudad = v.texto(datos, "ciudad", "la ciudad", 80, defecto=CIUDAD_POR_DEFECTO)
    latitud, longitud = v.coordenadas(datos)
    inicio = v.hora(datos, "ventana_inicio", "la hora inicial")
    fin = v.hora(datos, "ventana_fin", "la hora final")
    v.ventana(inicio, fin)

    if direccion:
        clave = normalizar_texto(direccion)
        repetida = any(
            d.direccion_normalizada == clave and normalizar_texto(d.ciudad) == normalizar_texto(ciudad)
            and (sede is None or d.id != sede.id)
            for d in cliente.direcciones
        )
        if repetida:
            v.invalido("direccion", "El cliente ya tiene registrada esa direccion.")
    v.lanzar_si_hay()
    if not etiqueta:
        etiqueta = sede.etiqueta if sede is not None else (
            "Principal" if not cliente.direcciones else f"Sede {len(cliente.direcciones) + 1}")
    return {"etiqueta": etiqueta, "direccion": direccion, "ciudad": ciudad, "latitud": latitud,
            "longitud": longitud, "ventana_inicio": inicio, "ventana_fin": fin}


def crear_sede(cliente, datos):
    limpios = validar_sede(cliente, datos)
    sede = DireccionCliente(cliente_id=cliente.id, activa=True, **limpios)
    cliente.direcciones.append(sede)
    db.session.add(sede)
    db.session.flush()
    return sede


def editar_sede(sede, datos):
    """Aplica los cambios y devuelve {campo: (antes, despues)}.

    Los pedidos ya creados conservan su copia historica de la direccion.
    """
    antes = _actuales(sede, CAMPOS_SEDE)
    limpios = validar_sede(sede.cliente, datos, sede)
    cambios = _cambios(antes, limpios)
    for campo, valor in limpios.items():
        setattr(sede, campo, valor)
    if "direccion" in cambios:
        sede.direccion_normalizada = normalizar_texto(sede.direccion)
    return cambios


def cambiar_estado_sede(sede, activa):
    """Una sede desactivada no se ofrece para pedidos nuevos."""
    if sede.activa == activa:
        raise DatosInvalidos([(None, f"La sede {sede.etiqueta} ya está "
                                     f"{'activa' if activa else 'desactivada'}.")])
    sede.activa = activa
