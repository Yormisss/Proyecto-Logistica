"""RF2 - Ingreso de Ordenes de Despacho y RF4 - Actualizacion de Estados en Terreno.

Incluye la prueba de entrega (Proof of Delivery) contemplada en el numeral 1.4 Alcances.
"""

from datetime import datetime

from app.extensions import db


class EstadoPedido:
    """Ciclo de vida de una orden de despacho (RF4)."""

    PENDIENTE = "PENDIENTE"    # Cargado, aun sin ruta asignada
    ASIGNADO = "ASIGNADO"      # Incluido en una ruta de un conductor
    EN_RUTA = "EN_RUTA"        # El conductor inicio el desplazamiento
    ENTREGADO = "ENTREGADO"    # Entrega exitosa -> descuenta inventario (RF5)
    FALLIDO = "FALLIDO"        # Entrega no lograda (cliente ausente, zona cerrada...)

    ETIQUETAS = {
        PENDIENTE: "Pendiente",
        ASIGNADO: "Asignado",
        EN_RUTA: "En ruta",
        ENTREGADO: "Entregado",
        FALLIDO: "Fallido",
    }

    ABIERTOS = (PENDIENTE, ASIGNADO, EN_RUTA)
    CERRADOS = (ENTREGADO, FALLIDO)

    # Transiciones validas; evita que un pedido entregado vuelva atras y
    # descuente inventario dos veces.
    TRANSICIONES = {
        PENDIENTE: (ASIGNADO,),
        ASIGNADO: (EN_RUTA, PENDIENTE),
        EN_RUTA: (ENTREGADO, FALLIDO),
        FALLIDO: (ASIGNADO, EN_RUTA),
        ENTREGADO: (),
    }

    @classmethod
    def puede_transicionar(cls, origen, destino):
        return destino in cls.TRANSICIONES.get(origen, ())


class Pedido(db.Model):
    __tablename__ = "pedidos"

    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(40), unique=True, nullable=False, index=True)

    # Cliente y sede a los que pertenece la orden (actor 2.2.4). Son las claves
    # que permiten consultar "los pedidos del cliente X" de forma exacta, en vez
    # de comparar nombres escritos a mano.
    cliente_id = db.Column(db.Integer, db.ForeignKey("clientes.id"), index=True)
    direccion_id = db.Column(db.Integer, db.ForeignKey("direcciones_cliente.id"))

    # --- Copia historica de los datos de entrega -------------------------------
    # Estos campos NO son redundancia por descuido: son el estado del destino en
    # el momento del despacho. Si el cliente traslada la sede, las claves ajenas
    # de arriba apuntarian a la direccion nueva y las entregas ya cerradas
    # mostrarian un destino al que nunca se fue, falseando la trazabilidad
    # (EventoPedido, PruebaEntrega) y los indicadores del RF6.
    cliente_nombre = db.Column(db.String(160), nullable=False)
    cliente_telefono = db.Column(db.String(30))
    direccion = db.Column(db.String(255), nullable=False)
    ciudad = db.Column(db.String(80), default="Bogota")
    latitud = db.Column(db.Float)
    longitud = db.Column(db.Float)

    # Ventanas horarias estrictas fijadas por clientes comerciales (numeral 1.4).
    # Se copian de la sede al crear el pedido, pero pueden negociarse por despacho.
    ventana_inicio = db.Column(db.Time)
    ventana_fin = db.Column(db.Time)

    estado = db.Column(
        db.String(20), nullable=False, default=EstadoPedido.PENDIENTE, index=True
    )
    fecha_despacho = db.Column(db.Date, nullable=False, index=True)
    prioridad = db.Column(db.Integer, default=3)  # 1 = mas alta
    observaciones = db.Column(db.Text)

    ruta_id = db.Column(db.Integer, db.ForeignKey("rutas.id"))
    orden_en_ruta = db.Column(db.Integer)  # Secuencia sugerida por la API (RF3)

    creado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    creado_en = db.Column(db.DateTime, default=datetime.utcnow)
    actualizado_en = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    inventario_descontado = db.Column(db.Boolean, nullable=False, default=False)

    ruta = db.relationship("Ruta", back_populates="pedidos")
    cliente = db.relationship("Cliente", back_populates="pedidos")
    direccion_entrega = db.relationship("DireccionCliente", back_populates="pedidos")
    creado_por = db.relationship("Usuario")
    items = db.relationship(
        "PedidoItem", back_populates="pedido", cascade="all, delete-orphan"
    )
    movimientos = db.relationship("MovimientoInventario", back_populates="pedido")
    eventos = db.relationship(
        "EventoPedido",
        back_populates="pedido",
        cascade="all, delete-orphan",
        order_by="EventoPedido.registrado_en",
    )
    prueba_entrega = db.relationship(
        "PruebaEntrega", back_populates="pedido", uselist=False, cascade="all, delete-orphan"
    )

    @property
    def estado_etiqueta(self):
        return EstadoPedido.ETIQUETAS.get(self.estado, self.estado)

    @property
    def tiene_coordenadas(self):
        return self.latitud is not None and self.longitud is not None

    @property
    def esta_cerrado(self):
        return self.estado in EstadoPedido.CERRADOS

    @property
    def ventana_texto(self):
        if self.ventana_inicio and self.ventana_fin:
            return f"{self.ventana_inicio.strftime('%H:%M')} - {self.ventana_fin.strftime('%H:%M')}"
        return "Sin restriccion"

    def __repr__(self):
        return f"<Pedido {self.codigo} {self.estado}>"


class PedidoItem(db.Model):
    """Detalle de productos por orden; base del descuento de inventario (RF5)."""

    __tablename__ = "pedido_items"

    id = db.Column(db.Integer, primary_key=True)
    pedido_id = db.Column(db.Integer, db.ForeignKey("pedidos.id"), nullable=False)
    producto_id = db.Column(db.Integer, db.ForeignKey("productos.id"), nullable=False)
    cantidad = db.Column(db.Integer, nullable=False, default=1)

    pedido = db.relationship("Pedido", back_populates="items")
    producto = db.relationship("Producto")

    def __repr__(self):
        return f"<PedidoItem pedido={self.pedido_id} prod={self.producto_id} x{self.cantidad}>"


class EventoPedido(db.Model):
    """Bitacora de cambios de estado: insumo para los KPIs de tiempos (RF6)."""

    __tablename__ = "eventos_pedido"

    id = db.Column(db.Integer, primary_key=True)
    pedido_id = db.Column(db.Integer, db.ForeignKey("pedidos.id"), nullable=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    estado_anterior = db.Column(db.String(20))
    estado_nuevo = db.Column(db.String(20), nullable=False)
    nota = db.Column(db.String(255))
    latitud = db.Column(db.Float)
    longitud = db.Column(db.Float)
    registrado_en = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    pedido = db.relationship("Pedido", back_populates="eventos")
    usuario = db.relationship("Usuario")


class PruebaEntrega(db.Model):
    """Proof of Delivery (PoD) - numeral 1.4 Alcances."""

    __tablename__ = "pruebas_entrega"

    id = db.Column(db.Integer, primary_key=True)
    pedido_id = db.Column(db.Integer, db.ForeignKey("pedidos.id"), unique=True, nullable=False)
    receptor_nombre = db.Column(db.String(160))
    receptor_documento = db.Column(db.String(40))
    observacion = db.Column(db.Text)
    motivo_fallo = db.Column(db.String(160))
    latitud = db.Column(db.Float)
    longitud = db.Column(db.Float)
    registrado_en = db.Column(db.DateTime, default=datetime.utcnow)

    pedido = db.relationship("Pedido", back_populates="prueba_entrega")
