"""Reglas de negocio compartidas por el generador, el ETL y el analisis."""

from pyspark.sql import functions as F

# Variantes de escritura de ciudad que llegan del ERP -> nombre canonico.
CIUDADES = ["Bogota", "Soacha", "Chia", "Funza", "Mosquera", "Cajica"]


def zona(lat, lng, ciudad):
    """Zona logistica: en Bogota se deriva de las coordenadas; el resto es periferia."""
    return (
        F.when(ciudad != "Bogota", F.lit("PERIFERIA"))
        .when(lat >= 4.70, F.lit("NORTE"))
        .when(lat < 4.58, F.lit("SUR"))
        .when(lng < -74.12, F.lit("OCCIDENTE"))
        .otherwise(F.lit("CENTRO"))
    )


def franja(hora):
    return (
        F.when(hora < 10, F.lit("MANANA"))
        .when(hora < 14, F.lit("MEDIODIA"))
        .when(hora < 17, F.lit("TARDE"))
        .otherwise(F.lit("NOCHE"))
    )
