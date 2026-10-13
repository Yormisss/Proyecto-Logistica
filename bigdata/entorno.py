"""Entorno comun del pipeline de Big Data: sesion de Spark y rutas del data lake.

El data lake se organiza por capas (arquitectura "medallion"):

    raw     datos tal como llegan de cada fuente (CSV, JSON)
    bronze  raw ingerido a Parquet, con metadatos de ingesta
    silver  datos limpios, tipados y deduplicados (resultado del ETL)
    gold    tablas analiticas listas para consulta, ML y Power BI (ELT)

Por defecto vive en `bigdata/datalake` (disco local). Para usar HDFS basta con
definir BIGDATA_LAKE=hdfs://namenode:9000/sgds: el codigo no cambia, porque Spark
resuelve el esquema de la URI.
"""

import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
LAKE = os.getenv("BIGDATA_LAKE", (RAIZ / "datalake").as_posix()).rstrip("/")
RESULTADOS = RAIZ / "resultados"

# En Windows, Spark necesita winutils.exe y hadoop.dll para escribir en disco.
_HADOOP = os.getenv("HADOOP_HOME") or str(RAIZ.parent.parent / "hadoop")


def ruta(capa, nombre=""):
    """URI de una tabla/carpeta dentro de la capa del lake."""
    return f"{LAKE}/{capa}/{nombre}".rstrip("/")


def crear_spark(nombre, particiones=8):
    if sys.platform == "win32" and Path(_HADOOP, "bin", "winutils.exe").exists():
        os.environ["HADOOP_HOME"] = _HADOOP
        os.environ["PATH"] = os.environ["PATH"] + os.pathsep + str(Path(_HADOOP, "bin"))
    # Driver y workers deben usar el mismo interprete de Python.
    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
    os.environ.setdefault("PYSPARK_DRIVER_PYTHON", sys.executable)

    from pyspark.sql import SparkSession

    spark = (
        SparkSession.builder.master(os.getenv("SPARK_MASTER", "local[*]"))
        .appName(f"SGDS - {nombre}")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.sql.shuffle.partitions", str(particiones))
        .config("spark.driver.memory", os.getenv("SPARK_MEMORIA", "3g"))
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("ERROR")
    return spark


def guardar_json(nombre, datos):
    """Escribe un resultado pequeno para la pantalla de la app y Power BI."""
    import json
    from decimal import Decimal

    def convertir(valor):
        # Spark devuelve DECIMAL para los promedios y porcentajes: deben seguir siendo numeros.
        return float(valor) if isinstance(valor, Decimal) else str(valor)

    RESULTADOS.mkdir(exist_ok=True)
    (RESULTADOS / nombre).write_text(
        json.dumps(datos, ensure_ascii=False, indent=2, default=convertir), encoding="utf-8"
    )
