"""Ingesta batch: raw (CSV/JSON de cada fuente) -> bronze (Parquet).

Bronze conserva los datos tal como llegaron (columnas como texto en los CSV) y
agrega metadatos de linaje: de que fuente y archivo vino cada fila y cuando se
ingirio. La limpieza no ocurre aqui sino en el ETL, de modo que siempre se puede
reprocesar desde el origen sin volver a pedirselo a las fuentes.
"""

from pyspark.sql import functions as F
from pyspark.sql.types import DoubleType, IntegerType, StringType, StructField, StructType

import entorno

ESQUEMA_EVENTO = StructType([
    StructField("codigo", StringType()), StructField("conductor_id", IntegerType()),
    StructField("latitud", DoubleType()), StructField("longitud", DoubleType()),
    StructField("estado_anterior", StringType()), StructField("estado_nuevo", StringType()),
    StructField("registrado_en", StringType()),
])


def _con_linaje(df, fuente):
    return (df.withColumn("_fuente", F.lit(fuente))
            .withColumn("_archivo", F.input_file_name())
            .withColumn("_ingestado_en", F.current_timestamp()))


def _csv(spark, nombre):
    # Sin inferSchema: todo entra como texto y el ETL decide los tipos.
    return spark.read.option("header", True).option("encoding", "UTF-8").csv(entorno.ruta("raw", nombre))


def ingerir(spark):
    conteos = {}

    pedidos = _con_linaje(_csv(spark, "erp_pedidos"), "ERP").unionByName(
        _con_linaje(_csv(spark, "app_pedidos"), "APP"))
    pedidos.write.mode("overwrite").parquet(entorno.ruta("bronze", "pedidos"))
    conteos["pedidos"] = spark.read.parquet(entorno.ruta("bronze", "pedidos")).count()

    leer = spark.read.schema(ESQUEMA_EVENTO).json
    eventos = _con_linaje(leer(entorno.ruta("raw", "app_eventos_sim")), "APP_MOVIL").unionByName(
        _con_linaje(leer(entorno.ruta("raw", "app_eventos")), "APP"))
    eventos.write.mode("overwrite").parquet(entorno.ruta("bronze", "eventos"))
    conteos["eventos"] = spark.read.parquet(entorno.ruta("bronze", "eventos")).count()

    mov = _con_linaje(_csv(spark, "wms_movimientos"), "WMS")
    mov.write.mode("overwrite").parquet(entorno.ruta("bronze", "movimientos"))
    conteos["movimientos"] = spark.read.parquet(entorno.ruta("bronze", "movimientos")).count()

    return conteos


if __name__ == "__main__":
    spark = entorno.crear_spark("Ingesta batch")
    print("Filas ingeridas a bronze:", ingerir(spark))
    spark.stop()
