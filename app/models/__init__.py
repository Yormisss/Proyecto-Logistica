"""Capa Modelo del patron MVC (RNF3).

Reexporta las entidades para que los controladores importen desde `app.models`.
"""

from app.models.auditoria import RESULTADO_ERROR, RESULTADO_EXITO, Auditoria
from app.models.asistente import VIGENCIA_CONFIRMACION, VIGENCIA_SESION, SesionAsistente
from app.models.cliente import Cliente, DireccionCliente, normalizar_texto
from app.models.configuracion import NOMBRE_OTRO, ConfiguracionNegocio, MotivoFallo
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
from app.models.solicitud import SolicitudContacto
from app.models.usuario import Rol, Usuario

__all__ = [
    "Auditoria",
    "RESULTADO_ERROR",
    "RESULTADO_EXITO",
    "ConfiguracionNegocio",
    "MotivoFallo",
    "NOMBRE_OTRO",
    "SesionAsistente",
    "SolicitudContacto",
    "VIGENCIA_CONFIRMACION",
    "VIGENCIA_SESION",
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
