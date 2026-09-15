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
