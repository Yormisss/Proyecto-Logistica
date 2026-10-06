"""Utilidades compartidas por las funciones del asistente de los distintos roles."""

from datetime import date

from flask import g

from app.services.busqueda_voz import describir, enumerar  # noqa: F401 (enumerar se reexporta)

# Mismo limite que PruebaEntrega.motivo_fallo; deja espacio en la nota de la
# bitacora (255) para el prefijo y "Registrado por el asistente de voz".
LARGO_MAXIMO_MOTIVO = 160


def texto(nombre):
    """Argumento de texto, sin espacios sobrantes ("" si no vino)."""
    valor = g.argumentos.get(nombre)
    return str(valor).strip() if valor is not None else ""


def entero(valor):
    """Entero dicho por voz: 3, 3.0 o "3". None si no es un entero."""
    if isinstance(valor, bool):
        return None
    if isinstance(valor, int):
        return valor
    if isinstance(valor, float) and valor.is_integer():
        return int(valor)
    if isinstance(valor, str) and valor.strip().lstrip("-").isdigit():
        return int(valor.strip())
    return None


def fecha_iso(valor, por_defecto):
    """Fecha "AAAA-MM-DD"; `por_defecto` si no vino. None si no se entiende."""
    if valor in (None, ""):
        return por_defecto
    try:
        return date.fromisoformat(str(valor).strip())
    except ValueError:
        return None


def fecha_voz(valor):
    return valor.strftime("%d/%m/%Y")


def porcentaje(valor):
    """58.3 -> "58,3 por ciento"; 100.0 -> "100 por ciento"."""
    if valor is None:
        return "sin datos"
    numero = f"{valor:.1f}".rstrip("0").rstrip(".").replace(".", ",")
    return f"{numero} por ciento"


def resolver(coincidencias, como_texto, que, no_encontrado):
    """(elemento, None) si hay uno solo; (None, mensaje para leer) si no."""
    if coincidencias.unico is not None:
        return coincidencias.unico, None
    if coincidencias.vacio:
        return None, no_encontrado
    return None, describir(coincidencias, como_texto, que)


def pedido_texto(pedido):
    return f"{pedido.codigo} de {pedido.cliente_nombre}"
