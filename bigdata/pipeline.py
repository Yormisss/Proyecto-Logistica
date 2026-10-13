"""Ejecuta el pipeline completo de Big Data de punta a punta.

    python bigdata/pipeline.py                      # 300.000 pedidos simulados
    python bigdata/pipeline.py --pedidos 1000000    # mas volumen
    python bigdata/pipeline.py --sin-generacion     # reutiliza el raw existente

Etapas: fuentes -> ingesta batch -> ETL (silver) -> ELT (gold) -> ML -> streaming.
Al terminar, la pantalla "Big Data" de la app muestra los resultados.
"""

import argparse
import time

import elt_gold
import entorno
import etl
import fuente_app
import fuente_generador
import ingesta
import ingesta_streaming
import ml_fallos


def _etapa(nombre, funcion):
    inicio = time.time()
    print(f"[{nombre}] ...", flush=True)
    resultado = funcion()
    print(f"[{nombre}] listo en {time.time() - inicio:.0f} s", flush=True)
    return resultado


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pedidos", type=int, default=300_000)
    ap.add_argument("--sin-generacion", action="store_true")
    args = ap.parse_args()

    spark = entorno.crear_spark("Pipeline completo")
    if not args.sin_generacion:
        _etapa("1 Fuentes: app real", fuente_app.exportar)
        _etapa("1 Fuentes: ERP, app movil y WMS simulados", lambda: fuente_generador.generar(spark, args.pedidos))
    print("   ", _etapa("2 Ingesta batch (raw -> bronze)", lambda: ingesta.ingerir(spark)))
    _etapa("3 ETL (bronze -> silver)", lambda: etl.ejecutar(spark))
    _etapa("4 ELT con Spark SQL (silver -> gold)", lambda: elt_gold.ejecutar(spark))
    _etapa("5 Modelo predictivo (MLlib)", lambda: ml_fallos.entrenar(spark))
    _etapa("6 Streaming de eventos", lambda: ingesta_streaming.ejecutar(spark))
    spark.stop()
    print("Pipeline completo. Resultados en bigdata/resultados y en la pantalla Big Data de la app.")
