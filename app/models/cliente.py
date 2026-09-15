"""Clientes comerciales y sus puntos de entrega.

Normaliza los datos que hasta ahora se repetian en cada fila de `pedidos`
(nombre, telefono, direccion y coordenadas del destinatario). Esos atributos
dependen del cliente, no de la orden, por lo que almacenarlos en el pedido
generaba una dependencia transitiva: el mismo destino se reescribia en cada
despacho y cualquier variacion de escritura creaba un "cliente" distinto.

Se separan dos conceptos que no son el mismo:

* `Cliente` es la entidad de negocio. Existe aunque nunca se registre en el
  portal, porque la mayoria llega por la importacion CSV del ERP legado.
* `Usuario` con rol CLIENTE es la credencial de acceso. Se vincula de forma
  opcional mediante `Cliente.usuario_id` (relacion 1 a 0..1), de modo que no
  hace falta inventar cuentas para poder cargar pedidos.
"""

import re
import unicodedata
from datetime import datetime

from app.extensions import db


def normalizar_texto(valor):
    """Clave de comparacion estable: sin acentos, sin puntuacion, minuscula.

    Es lo que evita que "Supermercado La 80", "SUPERMERCADO LA 80  " y
    "Supermercado  la 80." se registren como tres clientes diferentes al
    importar archivos generados a mano.
    """
    if not valor:
        return ""
    plano = unicodedata.normalize("NFKD", str(valor))
    plano = "".join(c for c in plano if not unicodedata.combining(c))
    plano = plano.lower().replace("&", " y ")
    plano = re.sub(r"[^a-z0-9]+", " ", plano)
    return plano.strip()


class Cliente(db.Model):
    """Destinatario comercial de las ordenes de despacho (actor 2.2.4)."""

    __tablename__ = "clientes"

    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(160), nullable=False)
    # Clave de deduplicacion derivada del nombre. Se indexa pero no se declara
    # unica: dos razones sociales homonimas con NIT distinto son legitimas y no
    # deben romper una importacion completa. La unicidad real la resuelve
    # `app.services.clientes.resolver_cliente`.
    nombre_normalizado = db.Column(db.String(160), nullable=False, index=True)
    # NIT o cedula. Es la clave natural del cliente cuando el ERP la exporta.
    documento = db.Column(db.String(30), unique=True)
    telefono = db.Column(db.String(30))
    correo = db.Column(db.String(120))

    # Cuenta de acceso al portal. Nullable: el cliente existe en la operacion
    # aunque no tenga usuario. Unique: una cuenta representa un solo cliente.
    usuario_id = db.Column(
        db.Integer, db.ForeignKey("usuarios.id"), unique=True, index=True
    )

    activo = db.Column(db.Boolean, nullable=False, default=True)
    creado_en = db.Column(db.DateTime, default=datetime.utcnow)

    usuario = db.relationship("Usuario", back_populates="cliente")
    direcciones = db.relationship(
        "DireccionCliente",
        back_populates="cliente",
        cascade="all, delete-orphan",
        order_by="DireccionCliente.etiqueta",
    )
    pedidos = db.relationship("Pedido", back_populates="cliente", lazy="dynamic")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if self.nombre and not self.nombre_normalizado:
            self.nombre_normalizado = normalizar_texto(self.nombre)

    def renombrar(self, nombre):
        """Mantiene sincronizada la clave de deduplicacion con el nombre."""
        self.nombre = nombre
        self.nombre_normalizado = normalizar_texto(nombre)

    @property
    def tiene_portal(self):
        return self.usuario_id is not None

    @property
    def direccion_principal(self):
        return self.direcciones[0] if self.direcciones else None

    def __repr__(self):
        return f"<Cliente {self.nombre}>"


class DireccionCliente(db.Model):
    """Punto de entrega de un cliente.

    Se modela como tabla hija porque un cliente comercial atiende varias sedes,
    cada una con coordenadas y ventana horaria propias; colapsarlas en `clientes`
    solo moveria de lugar la redundancia que se esta corrigiendo.
    """

    __tablename__ = "direcciones_cliente"

    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(
        db.Integer, db.ForeignKey("clientes.id"), nullable=False, index=True
    )

    etiqueta = db.Column(db.String(80), default="Principal")
    direccion = db.Column(db.String(255), nullable=False)
    direccion_normalizada = db.Column(db.String(255), nullable=False, index=True)
    ciudad = db.Column(db.String(80), default="Bogota")
    latitud = db.Column(db.Float)
    longitud = db.Column(db.Float)

    # Ventana horaria habitual de la sede. Sirve de valor por defecto al crear
    # un pedido; el pedido conserva la suya porque puede negociarse por despacho.
    ventana_inicio = db.Column(db.Time)
    ventana_fin = db.Column(db.Time)

    activa = db.Column(db.Boolean, nullable=False, default=True)
    creado_en = db.Column(db.DateTime, default=datetime.utcnow)

    cliente = db.relationship("Cliente", back_populates="direcciones")
    pedidos = db.relationship("Pedido", back_populates="direccion_entrega", lazy="dynamic")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if self.direccion and not self.direccion_normalizada:
            self.direccion_normalizada = normalizar_texto(self.direccion)

    @property
    def tiene_coordenadas(self):
        return self.latitud is not None and self.longitud is not None

    @property
    def texto_completo(self):
        return f"{self.direccion}, {self.ciudad}" if self.ciudad else self.direccion

    def __repr__(self):
        return f"<DireccionCliente {self.etiqueta}: {self.direccion}>"
