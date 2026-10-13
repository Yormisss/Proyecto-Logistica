"""ETL: bronze -> silver. Limpieza, tipado, deduplicacion y variables derivadas.

Cada regla cuenta cuantas filas toco, y ese conteo queda en
resultados/calidad_datos.json: es la evidencia de que el proceso corrige datos
reales y no solo los copia.
"""

from pyspark.sql import Window
from pyspark.sql import functions as F

import entorno
from reglas import franja, zona


def _ciudad_canonica(col):
    """Quita acentos, espacios y sufijos ("D.C.") y unifica mayusculas."""
    limpia = F.lower(F.trim(F.translate(col, "áéíóúÁÉÍÓÚ", "aeiouAEIOU")))
    return F.when(limpia.startswith("bogota"), F.lit("Bogota")).otherwise(F.initcap(limpia))


def limpiar_pedidos(spark):
    b = spark.read.parquet(entorno.ruta("bronze", "pedidos"))
    calidad = {"filas_entrada": b.count()}

    # 1. Sin codigo no hay forma de identificar el pedido: se descarta.
    c = b.filter(F.col("codigo").isNotNull() & (F.trim("codigo") != ""))
    calidad["sin_codigo_descartadas"] = calidad["filas_entrada"] - c.count()

    # 2. Duplicados exactos del export del ERP: se conserva la ultima ingestion.
    orden = Window.partitionBy("codigo").orderBy(F.col("_ingestado_en").desc())
    d = c.withColumn("_n", F.row_number().over(orden)).filter("_n = 1").drop("_n")

    # 3. Tipos.
    t = (
        d.withColumn("estado", F.upper(F.trim("estado")))
        .withColumn("ciudad_original", F.col("ciudad"))
        .withColumn("ciudad", _ciudad_canonica(F.col("ciudad")))
        .withColumn("fecha_despacho", F.to_date("fecha_despacho"))
        .withColumn("creado_en", F.to_timestamp("creado_en"))
        .withColumn("entregado_en", F.to_timestamp("entregado_en"))
        .withColumn("cliente_id", F.col("cliente_id").cast("int"))
        .withColumn("conductor_id", F.col("conductor_id").cast("int"))
        .withColumn("prioridad", F.col("prioridad").cast("int"))
        .withColumn("n_items", F.col("n_items").cast("int"))
        .withColumn("latitud", F.col("latitud").cast("double"))
        .withColumn("longitud", F.col("longitud").cast("double"))
        .withColumn("distancia_km", F.col("distancia_km").cast("double"))
        .withColumn("ventana_inicio", F.when(F.trim("ventana_inicio") != "", F.trim("ventana_inicio")))
        .withColumn("ventana_fin", F.when(F.trim("ventana_fin") != "", F.trim("ventana_fin")))
        .withColumn("motivo_fallo", F.when(F.trim("motivo_fallo") != "", F.trim("motivo_fallo")))
    ).cache()
    calidad["duplicados_eliminados"] = calidad["filas_entrada"] - calidad["sin_codigo_descartadas"] - t.count()

    # 4. Valores invalidos o ausentes.
    calidad["ciudades_normalizadas"] = t.filter(F.col("ciudad") != F.col("ciudad_original")).count()
    calidad["estados_en_minuscula"] = d.filter(F.col("estado") != F.upper("estado")).count()
    calidad["distancias_invalidas"] = t.filter(F.col("distancia_km") <= 0).count()
    calidad["prioridades_nulas"] = t.filter(F.col("prioridad").isNull()).count()
    calidad["clientes_sin_nombre"] = t.filter(F.col("cliente_nombre").isNull()).count()

    mediana = t.filter(F.col("distancia_km") > 0).approxQuantile("distancia_km", [0.5], 0.01)[0]
    calidad["mediana_distancia_imputada_km"] = mediana

    s = (
        t.withColumn("distancia_km", F.when(F.col("distancia_km") > 0, F.col("distancia_km")).otherwise(mediana))
        .withColumn("prioridad", F.coalesce("prioridad", F.lit(3)))
        .withColumn("cliente_nombre", F.coalesce("cliente_nombre", F.concat(F.lit("Cliente "), F.col("cliente_id"))))
        .filter(F.col("estado").isin("PENDIENTE", "ASIGNADO", "EN_RUTA", "ENTREGADO", "FALLIDO", "CANCELADO"))
    )

    # 5. Variables derivadas que usa el analisis.
    hora_ent = F.hour("entregado_en") + F.minute("entregado_en") / 60
    s = (
        s.withColumn("zona", zona(F.col("latitud"), F.col("longitud"), F.col("ciudad")))
        .withColumn("hora_entrega", F.round(hora_ent, 2))
        .withColumn("franja", franja(F.col("hora_entrega")))
        .withColumn("dia_semana", F.dayofweek("fecha_despacho"))
        .withColumn("anio", F.year("fecha_despacho"))
        .withColumn("mes", F.month("fecha_despacho"))
        .withColumn("fallido", (F.col("estado") == "FALLIDO").cast("int"))
        .withColumn("tiene_ventana", F.col("ventana_inicio").isNotNull())
        .withColumn("dentro_ventana", F.when(
            F.col("ventana_inicio").isNotNull() & F.col("entregado_en").isNotNull(),
            F.date_format("entregado_en", "HH:mm").between(F.col("ventana_inicio"), F.col("ventana_fin"))))
        .drop("ciudad_original")
    )
    s.write.mode("overwrite").partitionBy("anio", "mes").parquet(entorno.ruta("silver", "pedidos"))
    t.unpersist()

    calidad["filas_salida"] = spark.read.parquet(entorno.ruta("silver", "pedidos")).count()
    return calidad


def limpiar_eventos(spark):
    b = spark.read.parquet(entorno.ruta("bronze", "eventos"))
    e = (
        b.withColumn("registrado_en", F.to_timestamp("registrado_en"))
        .withColumn("estado_nuevo", F.upper(F.trim("estado_nuevo")))
        .filter(F.col("codigo").isNotNull() & F.col("registrado_en").isNotNull())
        .dropDuplicates(["codigo", "estado_nuevo", "registrado_en"])
    )
    e.write.mode("overwrite").parquet(entorno.ruta("silver", "eventos"))
    return {"eventos_entrada": b.count(), "eventos_salida": spark.read.parquet(entorno.ruta("silver", "eventos")).count()}


def limpiar_movimientos(spark):
    b = spark.read.parquet(entorno.ruta("bronze", "movimientos"))
    m = (
        b.withColumn("cantidad", F.col("cantidad").cast("int"))
        .withColumn("stock_resultante", F.col("stock_resultante").cast("int"))
        .withColumn("registrado_en", F.to_timestamp("registrado_en"))
        .withColumn("tipo", F.upper(F.trim("tipo")))
        .filter((F.col("cantidad") > 0) & F.col("sku").isNotNull() & F.col("registrado_en").isNotNull())
        .withColumn("anio", F.year("registrado_en")).withColumn("mes", F.month("registrado_en"))
    )
    m.write.mode("overwrite").partitionBy("anio", "mes").parquet(entorno.ruta("silver", "movimientos"))
    return {"movimientos_entrada": b.count(),
            "movimientos_salida": spark.read.parquet(entorno.ruta("silver", "movimientos")).count()}


def ejecutar(spark):
    calidad = limpiar_pedidos(spark)
    calidad.update(limpiar_eventos(spark))
    calidad.update(limpiar_movimientos(spark))
    entorno.guardar_json("calidad_datos.json", calidad)
    return calidad


if __name__ == "__main__":
    spark = entorno.crear_spark("ETL bronze a silver")
    for clave, valor in ejecutar(spark).items():
        print(f"  {clave}: {valor}")
    spark.stop()
