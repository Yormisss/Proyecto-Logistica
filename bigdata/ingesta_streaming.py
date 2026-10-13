"""Ingesta en tiempo real con Spark Structured Streaming.

Simula lo que ocurre en operacion: los celulares de los conductores reportan
cambios de estado y el sistema los procesa a medida que llegan. Una hebra deja
micro-lotes de eventos JSON en una carpeta de entrada; el stream los detecta,
los escribe en bronze y mantiene un conteo por estado en ventanas de 30 segundos.

En produccion, la carpeta se reemplaza por un topico de Kafka cambiando solo la
fuente del stream:

    spark.readStream.format("kafka")
         .option("kafka.bootstrap.servers", "broker:9092")
         .option("subscribe", "eventos_pedido").load()
"""

import json
import random
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from pyspark.sql import functions as F

import entorno
from ingesta import ESQUEMA_EVENTO

TRANSICIONES = [("PENDIENTE", "ASIGNADO"), ("ASIGNADO", "EN_RUTA"), ("EN_RUTA", "ENTREGADO"),
                ("EN_RUTA", "ENTREGADO"), ("EN_RUTA", "FALLIDO")]


def _local(uri):
    return Path(uri.replace("file:///", ""))


def _emisor(carpeta, lotes, por_lote, pausa):
    """Simula los celulares: cada `pausa` segundos llega un lote de eventos."""
    for n in range(lotes):
        eventos = []
        for i in range(por_lote):
            antes, despues = random.choice(TRANSICIONES)
            eventos.append({
                "codigo": f"STREAM-{n:02d}{i:03d}", "conductor_id": random.randint(1, 60),
                "latitud": round(4.45 + random.random() * 0.35, 5),
                "longitud": round(-74.22 + random.random() * 0.17, 5),
                "estado_anterior": antes, "estado_nuevo": despues,
                "registrado_en": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            })
        temporal = carpeta / f"_lote_{n}.tmp"
        temporal.write_text("\n".join(json.dumps(e) for e in eventos), encoding="utf-8")
        temporal.rename(carpeta / f"lote_{n}.json")  # aparece completo de una vez
        time.sleep(pausa)


def ejecutar(spark, lotes=6, por_lote=40, pausa=3):
    entrada = _local(entorno.ruta("raw", "stream_entrada"))
    for limpio in (entrada, _local(entorno.ruta("bronze", "eventos_stream")),
                   _local(entorno.ruta("bronze", "_checkpoint_stream"))):
        shutil.rmtree(limpio, ignore_errors=True)
    entrada.mkdir(parents=True)

    flujo = (spark.readStream.schema(ESQUEMA_EVENTO).json(entrada.as_posix())
             .withColumn("ts", F.to_timestamp("registrado_en")))

    # Consulta 1: persiste cada evento en bronze apenas llega.
    guardado = (flujo.writeStream.format("parquet")
                .option("path", entorno.ruta("bronze", "eventos_stream"))
                .option("checkpointLocation", entorno.ruta("bronze", "_checkpoint_stream"))
                .outputMode("append").trigger(processingTime="2 seconds").start())

    # Consulta 2: conteo por estado en ventanas de 30 s, disponible como tabla SQL.
    conteo = (flujo.groupBy(F.window("ts", "30 seconds").alias("ventana"), "estado_nuevo").count()
              .select(F.col("ventana.start").alias("inicio"), "estado_nuevo", "count"))
    vivo = (conteo.writeStream.format("memory").queryName("estados_en_vivo")
            .outputMode("complete").trigger(processingTime="2 seconds").start())

    hilo = threading.Thread(target=_emisor, args=(entrada, lotes, por_lote, pausa), daemon=True)
    hilo.start()
    hilo.join()
    guardado.processAllAvailable()
    vivo.processAllAvailable()

    resumen = [list(r) for r in spark.sql(
        "SELECT estado_nuevo, SUM(count) AS eventos FROM estados_en_vivo GROUP BY estado_nuevo ORDER BY eventos DESC"
    ).collect()]
    ventanas = [[str(r[0]), r[1], r[2]] for r in spark.sql(
        "SELECT inicio, estado_nuevo, count FROM estados_en_vivo ORDER BY inicio, estado_nuevo").collect()]
    persistidos = spark.read.parquet(entorno.ruta("bronze", "eventos_stream")).count()
    guardado.stop()
    vivo.stop()

    resultado = {"lotes": lotes, "eventos_emitidos": lotes * por_lote, "eventos_persistidos": persistidos,
                 "por_estado": resumen, "ventanas_30s": ventanas}
    entorno.guardar_json("streaming.json", resultado)
    return resultado


if __name__ == "__main__":
    spark = entorno.crear_spark("Streaming de eventos")
    r = ejecutar(spark)
    print(f"Emitidos {r['eventos_emitidos']} / persistidos {r['eventos_persistidos']}")
    print("Por estado:", r["por_estado"])
    spark.stop()
