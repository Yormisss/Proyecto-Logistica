"""Fuentes 2, 3 y 4: ERP del cliente, app movil de conductores y almacen (WMS).

La app real solo tiene unos cientos de pedidos de demostracion. Para trabajar con
una operacion del tamano de una flota de verdad, este generador produce el
historico de dos anos en Spark (distribuido, sin bucles de Python) con el mismo
esquema de la app, e inyecta los defectos tipicos de datos reales para que el ETL
tenga algo que limpiar: duplicados, ciudades escritas distinto, nulos, distancias
negativas y estados en minuscula.

    python fuente_generador.py --pedidos 300000
"""

import argparse

from pyspark.sql import functions as F

import entorno
from reglas import zona

SEMILLA = 42
N_CONDUCTORES = 60
N_CLIENTES = 3000
N_SKU = 80
MOTIVOS = ["Cliente ausente", "Direccion errada", "Zona cerrada", "Pedido rechazado", "Sin acceso"]

COLUMNAS = ["codigo", "cliente_id", "cliente_nombre", "ciudad", "latitud", "longitud", "fecha_despacho",
            "prioridad", "ventana_inicio", "ventana_fin", "estado", "conductor_id", "ruta_codigo",
            "distancia_km", "n_items", "creado_en", "entregado_en", "motivo_fallo"]


def _pedidos(spark, n):
    def r(k):
        return F.rand(SEMILLA + k)

    df = spark.range(n).withColumnRenamed("id", "n")

    u = r(1)
    df = df.withColumn(
        "ciudad",
        F.when(u < 0.82, "Bogota").when(u < 0.88, "Soacha").when(u < 0.92, "Chia")
        .when(u < 0.95, "Funza").when(u < 0.98, "Mosquera").otherwise("Cajica"),
    )
    # Coordenadas: Bogota ocupa una caja amplia, la periferia se agrupa cerca de su centro.
    centro = (
        F.when(F.col("ciudad") == "Soacha", F.array(F.lit(4.58), F.lit(-74.22)))
        .when(F.col("ciudad") == "Chia", F.array(F.lit(4.86), F.lit(-74.03)))
        .when(F.col("ciudad") == "Funza", F.array(F.lit(4.72), F.lit(-74.21)))
        .when(F.col("ciudad") == "Mosquera", F.array(F.lit(4.70), F.lit(-74.23)))
        .otherwise(F.array(F.lit(4.88), F.lit(-74.02)))
    )
    es_bogota = F.col("ciudad") == "Bogota"
    df = (
        df.withColumn("latitud", F.when(es_bogota, 4.45 + r(2) * 0.35).otherwise(centro[0] + (r(2) - 0.5) * 0.06))
        .withColumn("longitud", F.when(es_bogota, -74.22 + r(3) * 0.17).otherwise(centro[1] + (r(3) - 0.5) * 0.06))
        .withColumn("zona", zona(F.col("latitud"), F.col("longitud"), F.col("ciudad")))
        .withColumn("cliente_id", (r(4) * N_CLIENTES).cast("int") + 1)
        .withColumn("conductor_id", (r(5) * N_CONDUCTORES).cast("int") + 1)
        .withColumn("prioridad", (r(6) * 5).cast("int") + 1)
        .withColumn("n_items", (r(7) * 5).cast("int") + 1)
        .withColumn("distancia_km", F.round(2 + r(8) * 28, 1))
        .withColumn("fecha_despacho", F.date_add(F.lit("2024-10-13").cast("date"), (r(9) * 730).cast("int")))
    )
    # Ventana horaria: 80% de los pedidos la tienen (franja de 2 horas).
    hora_ini = (7 + r(10) * 9).cast("int")
    tiene_ventana = r(11) < 0.8
    df = df.withColumn("_h_ini", F.when(tiene_ventana, hora_ini))
    # Hora real de entrega (decimal): dentro o un poco despues de la ventana.
    df = df.withColumn("_hora_ent", F.when(tiene_ventana, hora_ini + r(12) * 3.2).otherwise(8 + r(12) * 10))

    # Probabilidad de fallo: depende de zona, hora, prioridad, distancia y conductor.
    p = (
        F.lit(0.06)
        + F.when(F.col("zona").isin("SUR", "OCCIDENTE"), 0.10).otherwise(0.0)
        + F.when(F.col("zona") == "PERIFERIA", 0.06).otherwise(0.0)
        + F.when(F.col("_hora_ent") >= 16, 0.08).otherwise(0.0)
        + F.when(F.col("prioridad") >= 4, 0.04).otherwise(0.0)
        + F.when(F.col("distancia_km") > 20, 0.05).otherwise(0.0)
        + F.when(F.col("conductor_id") % 10 == 0, 0.07).otherwise(0.0)
    )
    df = df.withColumn(
        "estado",
        F.when(r(13) < 0.03, "CANCELADO").when(r(14) < p, "FALLIDO").otherwise("ENTREGADO"),
    ).withColumn(
        "motivo_fallo",
        F.when(F.col("estado") == "FALLIDO", F.array(*[F.lit(m) for m in MOTIVOS])[(r(15) * 5).cast("int")]),
    )

    medianoche = F.col("fecha_despacho").cast("timestamp").cast("long")
    df = (
        df.withColumn("codigo", F.concat(F.lit("SIM-"), F.lpad(F.col("n").cast("string"), 8, "0")))
        .withColumn("cliente_nombre", F.concat(F.lit("Cliente "), F.col("cliente_id").cast("string")))
        .withColumn("ruta_codigo", F.concat(F.lit("RUT-"), F.date_format("fecha_despacho", "yyyyMMdd"),
                                            F.lit("-"), F.lpad(F.col("conductor_id").cast("string"), 2, "0")))
        .withColumn("ventana_inicio", F.when(F.col("_h_ini").isNotNull(), F.format_string("%02d:00", F.col("_h_ini"))))
        .withColumn("ventana_fin", F.when(F.col("_h_ini").isNotNull(), F.format_string("%02d:00", F.col("_h_ini") + 2)))
        .withColumn("creado_en", (medianoche - ((1 + r(16) * 20) * 3600).cast("long")).cast("timestamp"))
        .withColumn("entregado_en", F.when(F.col("estado") != "CANCELADO",
                                           (medianoche + (F.col("_hora_ent") * 3600).cast("long")).cast("timestamp")))
        .drop("_h_ini", "_hora_ent")
    )
    return df


def _eventos(p):
    """Bitacora de estados (equivalente a EventoPedido), 1 a 3 eventos por pedido."""
    base = ["codigo", "conductor_id", "latitud", "longitud"]
    no_cancelado = p.filter(F.col("estado") != "CANCELADO")
    asignado = no_cancelado.select(
        *base, F.lit("PENDIENTE").alias("estado_anterior"), F.lit("ASIGNADO").alias("estado_nuevo"),
        (F.col("entregado_en").cast("long") - 3600 * 3).cast("timestamp").alias("registrado_en"))
    en_ruta = no_cancelado.select(
        *base, F.lit("ASIGNADO").alias("estado_anterior"), F.lit("EN_RUTA").alias("estado_nuevo"),
        (F.col("entregado_en").cast("long") - 1500).cast("timestamp").alias("registrado_en"))
    final = no_cancelado.select(
        *base, F.lit("EN_RUTA").alias("estado_anterior"), F.col("estado").alias("estado_nuevo"),
        F.col("entregado_en").alias("registrado_en"))
    cancelado = p.filter(F.col("estado") == "CANCELADO").select(
        *base, F.lit("PENDIENTE").alias("estado_anterior"), F.lit("CANCELADO").alias("estado_nuevo"),
        F.col("creado_en").alias("registrado_en"))
    return asignado.unionByName(en_ruta).unionByName(final).unionByName(cancelado)


def _movimientos(p):
    """Movimientos del almacen: una salida por entrega y reposiciones periodicas."""
    def r(k):
        return F.rand(SEMILLA + 100 + k)

    def sku(k):
        return F.concat(F.lit("SKU-"), F.lpad(((r(k) * N_SKU).cast("int") + 1).cast("string"), 3, "0"))

    salidas = p.filter(F.col("estado") == "ENTREGADO").select(
        sku(1).alias("sku"), F.col("codigo").alias("pedido_codigo"), F.lit("SALIDA").alias("tipo"),
        F.col("n_items").alias("cantidad"), (r(2) * 500).cast("int").alias("stock_resultante"),
        F.col("entregado_en").alias("registrado_en"))
    entradas = p.filter(F.rand(SEMILLA + 200) < 0.04).select(
        sku(5).alias("sku"), F.lit(None).cast("string").alias("pedido_codigo"), F.lit("ENTRADA").alias("tipo"),
        (20 + r(3) * 180).cast("int").alias("cantidad"), (200 + r(4) * 400).cast("int").alias("stock_resultante"),
        F.col("creado_en").alias("registrado_en"))
    return salidas.unionByName(entradas)


def _ensuciar(p):
    """Defectos tipicos de un export de ERP, en proporciones realistas."""
    u = F.rand(SEMILLA + 300)
    ciudad = (
        F.when(F.col("ciudad") != "Bogota", F.col("ciudad"))
        .when(u < 0.02, "BOGOTA").when(u < 0.035, " bogota ").when(u < 0.05, "Bogotá")
        .when(u < 0.06, "Bogota D.C.").otherwise("Bogota")
    )
    sucio = (
        p.withColumn("ciudad", ciudad)
        .withColumn("prioridad", F.when(F.rand(SEMILLA + 301) < 0.01, None).otherwise(F.col("prioridad")))
        .withColumn("distancia_km", F.when(F.rand(SEMILLA + 302) < 0.005, -F.col("distancia_km"))
                    .otherwise(F.col("distancia_km")))
        .withColumn("estado", F.when(F.rand(SEMILLA + 303) < 0.01, F.lower("estado")).otherwise(F.col("estado")))
        .withColumn("cliente_nombre", F.when(F.rand(SEMILLA + 304) < 0.005, None).otherwise(F.col("cliente_nombre")))
    )
    duplicados = sucio.sample(fraction=0.02, seed=SEMILLA)
    return sucio.unionByName(duplicados)


def generar(spark, n):
    p = _pedidos(spark, n).cache()
    (_ensuciar(p.select(*COLUMNAS)).repartition(4).write.mode("overwrite").option("header", True)
     .csv(entorno.ruta("raw", "erp_pedidos")))
    _eventos(p).repartition(4).write.mode("overwrite").json(entorno.ruta("raw", "app_eventos_sim"))
    _movimientos(p).repartition(2).write.mode("overwrite").option("header", True).csv(
        entorno.ruta("raw", "wms_movimientos"))
    p.unpersist()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pedidos", type=int, default=300_000)
    cantidad = ap.parse_args().pedidos
    spark = entorno.crear_spark("Generador de fuentes")
    generar(spark, cantidad)
    print(f"Fuentes generadas: {cantidad:,} pedidos (+ eventos y movimientos) en {entorno.ruta('raw')}")
    spark.stop()
