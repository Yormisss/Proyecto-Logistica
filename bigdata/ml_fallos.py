"""Modelo predictivo (Spark MLlib): probabilidad de que una entrega falle.

Usa solo variables conocidas al momento de planificar la ruta (zona, hora
planeada, prioridad, distancia, items, dia, conductor): asi el modelo sirve para
decidir *antes* de despachar, por ejemplo reasignando los pedidos de mayor riesgo.
No usa la hora real de entrega, que es consecuencia del resultado y filtraria la
respuesta al modelo.
"""

from pyspark.ml import Pipeline
from pyspark.ml.classification import RandomForestClassifier
from pyspark.ml.evaluation import BinaryClassificationEvaluator
from pyspark.ml.feature import StringIndexer, VectorAssembler
from pyspark.sql import functions as F

import entorno

CATEGORICAS = ["zona", "ciudad"]
NUMERICAS = ["hora_planeada", "prioridad", "distancia_km", "n_items", "dia_semana", "sin_ventana",
             "conductor_id", "tasa_fallo_conductor"]


def entrenar(spark):
    d = spark.read.parquet(entorno.ruta("silver", "pedidos")).filter(F.col("estado").isin("ENTREGADO", "FALLIDO"))
    d = (
        d.filter(F.col("conductor_id").isNotNull())
        .withColumn("hora_planeada", F.coalesce(F.substring("ventana_inicio", 1, 2).cast("int"), F.lit(12)).cast("double"))
        .withColumn("sin_ventana", (~F.col("tiene_ventana")).cast("double"))
        .withColumn("label", F.col("fallido").cast("double"))
        .select("label", *CATEGORICAS, *[c for c in NUMERICAS if c != "tasa_fallo_conductor"])
        .fillna(0)
    )
    entrenamiento, prueba = d.randomSplit([0.8, 0.2], seed=7)

    # Historial del conductor: tasa de fallo previa, calculada solo con entrenamiento
    # para que la prueba no vea sus propias respuestas. Un conductor nuevo recibe la media.
    media = entrenamiento.agg(F.avg("label")).first()[0]
    historial = entrenamiento.groupBy("conductor_id").agg(F.avg("label").alias("tasa_fallo_conductor"))
    agregar = lambda df: (df.join(historial, "conductor_id", "left")
                          .withColumn("tasa_fallo_conductor", F.coalesce("tasa_fallo_conductor", F.lit(media))))
    entrenamiento, prueba = agregar(entrenamiento), agregar(prueba)

    indexadores = [StringIndexer(inputCol=c, outputCol=f"{c}_idx", handleInvalid="keep") for c in CATEGORICAS]
    columnas = [f"{c}_idx" for c in CATEGORICAS] + NUMERICAS
    ensamblador = VectorAssembler(inputCols=columnas, outputCol="features")
    # maxBins >= cardinalidad de conductor_id (60) para tratarlo con division por umbral.
    # Los fallos son minoritarios (~15%): se balancean con un peso por clase.
    tasa = entrenamiento.agg(F.avg("label")).first()[0]
    peso = F.when(F.col("label") == 1.0, F.lit((1 - tasa) / tasa)).otherwise(F.lit(1.0))
    rf = RandomForestClassifier(labelCol="label", featuresCol="features", weightCol="peso",
                                numTrees=60, maxDepth=7, maxBins=64, seed=7)
    modelo = Pipeline(stages=[*indexadores, ensamblador, rf]).fit(entrenamiento.withColumn("peso", peso))

    pred = modelo.transform(prueba)
    auc = BinaryClassificationEvaluator(labelCol="label", metricName="areaUnderROC").evaluate(pred)
    cm = {(int(r["label"]), int(r["prediction"])): r["count"] for r in pred.groupBy("label", "prediction").count().collect()}
    vp, fp, fn, vn = cm.get((1, 1), 0), cm.get((0, 1), 0), cm.get((1, 0), 0), cm.get((0, 0), 0)

    importancias = sorted(zip(columnas, modelo.stages[-1].featureImportances.toArray()), key=lambda x: -x[1])
    resultado = {
        "modelo": "RandomForestClassifier (Spark MLlib)",
        "filas_entrenamiento": entrenamiento.count(), "filas_prueba": prueba.count(),
        "tasa_fallo_base_pct": round(tasa * 100, 2),
        "auc": round(auc, 4),
        "exactitud": round((vp + vn) / max(vp + vn + fp + fn, 1), 4),
        "precision_fallos": round(vp / max(vp + fp, 1), 4),
        "recall_fallos": round(vp / max(vp + fn, 1), 4),
        "matriz_confusion": {"verdaderos_positivos": vp, "falsos_positivos": fp,
                             "falsos_negativos": fn, "verdaderos_negativos": vn},
        "importancia_variables": [{"variable": v, "importancia": round(float(i), 4)} for v, i in importancias],
    }
    entorno.guardar_json("ml_fallos.json", resultado)
    return resultado


if __name__ == "__main__":
    spark = entorno.crear_spark("ML fallos")
    r = entrenar(spark)
    print({k: v for k, v in r.items() if k != "importancia_variables"})
    print(r["importancia_variables"])
    spark.stop()
