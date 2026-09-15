"""Configuracion de la aplicacion.

RNF3 - Arquitectura y Despliegue: configuracion separada por entorno para permitir
alojamiento local (desarrollo) o en plataformas cloud gratuitas (produccion).
RNF4 - Integrabilidad: el motor de base de datos se define por variable de entorno,
por lo que el mismo modelo de datos corre sobre SQLite (desarrollo) o MySQL (piloto).
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


class ConfiguracionBase:
    SECRET_KEY = os.getenv("SECRET_KEY", "clave-de-desarrollo-cambiar-en-produccion")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # RF3 - Generacion de rutas: proveedor de geolocalizacion y su clave.
    PROVEEDOR_MAPAS = os.getenv("PROVEEDOR_MAPAS", "OSRM")
    MAPAS_API_KEY = os.getenv("MAPAS_API_KEY", "")
    # Instancia de OSRM a consultar. Vacio = servidor publico del proyecto.
    # Defina esta variable para apuntar a una instancia propia (recomendado en
    # produccion: el servidor publico no ofrece garantias de disponibilidad).
    URL_OSRM = os.getenv("URL_OSRM", "")

    # Origen de las rutas: coordenadas del centro de distribucion.
    CD_NOMBRE = os.getenv("CD_NOMBRE", "Centro de Distribucion Bogota")
    CD_LAT = float(os.getenv("CD_LAT", "4.6482837"))
    CD_LNG = float(os.getenv("CD_LNG", "-74.2478938"))


class ConfiguracionDesarrollo(ConfiguracionBase):
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL", f"sqlite:///{BASE_DIR / 'instance' / 'logistica.db'}"
    )


class ConfiguracionProduccion(ConfiguracionBase):
    DEBUG = False
    # Ej: mysql+pymysql://usuario:clave@servidor:3306/logistica
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", "")
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True


class ConfiguracionPruebas(ConfiguracionBase):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


CONFIGURACIONES = {
    "desarrollo": ConfiguracionDesarrollo,
    "produccion": ConfiguracionProduccion,
    "pruebas": ConfiguracionPruebas,
}
