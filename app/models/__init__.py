"""Capa Modelo del patron MVC (RNF3).

Reexporta las entidades para que los controladores importen desde `app.models`.
"""

from app.models.cliente import Cliente, DireccionCliente, normalizar_texto
from app.models.inventario import MovimientoInventario, Producto, TipoMovimiento
from app.models.metrica import (
    ENDPOINTS_CRITICOS,
    UMBRAL_MAXIMO_MS,
    UMBRAL_OBJETIVO_MS,
    MedicionRendimiento,
)
from app.models.pedido import (
    EstadoPedido,
    EventoPedido,
    Pedido,
    PedidoItem,
    PruebaEntrega,
)
from app.models.ruta import EstadoRuta, Ruta, Vehiculo
from app.models.usuario import Rol, Usuario

__all__ = [
    "Cliente",
    "DireccionCliente",
    "normalizar_texto",
    "MovimientoInventario",
    "MedicionRendimiento",
    "ENDPOINTS_CRITICOS",
    "UMBRAL_OBJETIVO_MS",
    "UMBRAL_MAXIMO_MS",
    "Producto",
    "TipoMovimiento",
    "EstadoPedido",
    "EventoPedido",
    "Pedido",
    "PedidoItem",
    "PruebaEntrega",
    "EstadoRuta",
    "Ruta",
    "Vehiculo",
    "Rol",
    "Usuario",
]
