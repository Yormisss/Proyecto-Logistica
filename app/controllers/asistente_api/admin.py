"""Funciones del asistente de voz del administrador: el panel de rendimiento.

El admin usa ademas todas las del gestor (gestor.py las declara para ambos
roles), incluidos los indicadores de la analitica, y las de administracion.py
(usuarios, configuracion y bitacora). El panel de rendimiento es solo suyo,
como en pantalla.
"""

from flask import g

from app.controllers.asistente_api import cantidad, funcion_asistente, responder
from app.controllers.asistente_api.comun import entero, enumerar, porcentaje
from app.models import Rol
from app.services import analitica

ESPACIO = "admin"
ROLES = (Rol.ADMIN,)

# Mismos periodos que el panel de rendimiento.
PERIODOS_HORAS = (1, 24, 168)
HORAS_POR_DEFECTO = 24

LIMITE_LISTA = 5


@funcion_asistente(
    ESPACIO, "resumen-rendimiento", roles=ROLES,
    descripcion="Resumen del panel de rendimiento: tiempos de respuesta de las transacciones "
                "criticas y su cumplimiento del limite.",
    parametros={"horas": {"type": "integer",
                          "description": "Ventana en horas: 1, 24 (por defecto) o 168."}},
)
def resumen_rendimiento():
    horas = entero(g.argumentos.get("horas"))
    horas = horas if horas in PERIODOS_HORAS else HORAS_POR_DEFECTO
    datos = analitica.rendimiento(horas)
    if not datos["muestras"]:
        return responder(f"No hay mediciones de rendimiento en las últimas {horas} horas.")

    partes = [
        f"En las últimas {horas} horas hay {cantidad(datos['muestras'], 'medición', 'mediciones')}: "
        f"mediana de {datos['mediana_ms']:.0f} milisegundos, percentil 95 de "
        f"{datos['p95_ms']:.0f} y máximo de {datos['maximo_ms']:.0f}. "
        f"Cumple el límite el {porcentaje(datos['porcentaje_cumplimiento'])}."
    ]
    lentas = [t["nombre"] for t in datos["transacciones"] if not t["cumple"]]
    if lentas:
        partes.append("Superan el límite: " + enumerar(lentas[:LIMITE_LISTA]) + ".")
    return responder(" ".join(partes))
