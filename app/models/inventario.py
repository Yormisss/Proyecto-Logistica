"""Modelo de almacenamiento: productos y movimientos de inventario.

Responde al objetivo especifico 1.3.1 (sincronizar el inventario fisico del almacen
con los despachos) y soporta el RF5 - Sincronizacion Logica de Inventario.
"""

from datetime import datetime

from app.extensions import db


class Producto(db.Model):
    __tablename__ = "productos"

    id = db.Column(db.Integer, primary_key=True)
    sku = db.Column(db.String(40), unique=True, nullable=False, index=True)
    nombre = db.Column(db.String(160), nullable=False)
    descripcion = db.Column(db.String(255))
    unidad = db.Column(db.String(20), default="UND")
    stock_actual = db.Column(db.Integer, nullable=False, default=0)
    stock_minimo = db.Column(db.Integer, nullable=False, default=0)
    activo = db.Column(db.Boolean, nullable=False, default=True)
    creado_en = db.Column(db.DateTime, default=datetime.utcnow)

    movimientos = db.relationship(
        "MovimientoInventario", back_populates="producto", lazy="dynamic"
    )

    @property
    def bajo_minimo(self):
        """Alerta de quiebre de stock (cadena causal 2.3.2)."""
        return self.stock_actual <= self.stock_minimo

    def __repr__(self):
        return f"<Producto {self.sku} stock={self.stock_actual}>"


class TipoMovimiento:
    ENTRADA = "ENTRADA"
    SALIDA = "SALIDA"
    AJUSTE = "AJUSTE"
    REVERSION = "REVERSION"


class MovimientoInventario(db.Model):
    """Trazabilidad de cada afectacion al stock.

    Deja evidencia auditable de por que cambio el inventario, eliminando el manejo
    fragmentado de la informacion descrito en el numeral 2.1.
    """

    __tablename__ = "movimientos_inventario"

    id = db.Column(db.Integer, primary_key=True)
    producto_id = db.Column(db.Integer, db.ForeignKey("productos.id"), nullable=False)
    pedido_id = db.Column(db.Integer, db.ForeignKey("pedidos.id"))
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    tipo = db.Column(db.String(20), nullable=False)
    cantidad = db.Column(db.Integer, nullable=False)
    stock_resultante = db.Column(db.Integer, nullable=False)
    motivo = db.Column(db.String(255))
    registrado_en = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    producto = db.relationship("Producto", back_populates="movimientos")
    pedido = db.relationship("Pedido", back_populates="movimientos")
    usuario = db.relationship("Usuario")

    def __repr__(self):
        return f"<Movimiento {self.tipo} {self.cantidad} prod={self.producto_id}>"
