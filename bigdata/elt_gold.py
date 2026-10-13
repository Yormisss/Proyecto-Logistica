"""ELT: silver -> gold con Spark SQL. Tablas analiticas del almacen de datos.

Aqui el patron es ELT: los datos ya estan cargados (silver) y la transformacion
final se expresa en SQL sobre el motor de Spark. Cada tabla gold se guarda en
Parquet (lake) y como CSV en resultados/powerbi/ para conectarla a Power BI.
"""

import csv

import entorno

CONSULTAS = {
    "kpi_mensual": """
        SELECT anio, mes, COUNT(*) AS pedidos,
               SUM(CASE WHEN estado = 'ENTREGADO' THEN 1 ELSE 0 END) AS entregados,
               SUM(CASE WHEN estado = 'FALLIDO' THEN 1 ELSE 0 END) AS fallidos,
               SUM(CASE WHEN estado = 'CANCELADO' THEN 1 ELSE 0 END) AS cancelados,
               ROUND(100.0 * SUM(CASE WHEN estado = 'ENTREGADO' THEN 1 ELSE 0 END)
                     / NULLIF(SUM(CASE WHEN estado IN ('ENTREGADO','FALLIDO') THEN 1 ELSE 0 END), 0), 2) AS tasa_exito,
               ROUND(100.0 * SUM(CASE WHEN dentro_ventana THEN 1 ELSE 0 END)
                     / NULLIF(SUM(CASE WHEN dentro_ventana IS NOT NULL THEN 1 ELSE 0 END), 0), 2) AS cumplimiento_ventana,
               ROUND(AVG(distancia_km), 2) AS km_promedio
        FROM pedidos GROUP BY anio, mes ORDER BY anio, mes""",

    "fallos_zona_franja": """
        SELECT zona, franja, COUNT(*) AS intentos, SUM(fallido) AS fallidos,
               ROUND(100.0 * SUM(fallido) / COUNT(*), 2) AS tasa_fallo
        FROM pedidos WHERE estado IN ('ENTREGADO','FALLIDO')
        GROUP BY zona, franja ORDER BY tasa_fallo DESC""",

    "fallos_ciudad": """
        SELECT ciudad, COUNT(*) AS pedidos, SUM(fallido) AS fallidos,
               ROUND(100.0 * SUM(fallido) / COUNT(*), 2) AS tasa_fallo
        FROM pedidos WHERE estado IN ('ENTREGADO','FALLIDO')
        GROUP BY ciudad ORDER BY pedidos DESC""",

    "motivos_fallo": """
        SELECT motivo_fallo AS motivo, COUNT(*) AS casos,
               ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS porcentaje
        FROM pedidos WHERE estado = 'FALLIDO' AND motivo_fallo IS NOT NULL
        GROUP BY motivo_fallo ORDER BY casos DESC""",

    "rendimiento_conductor": """
        SELECT conductor_id, COUNT(*) AS intentos, SUM(fallido) AS fallidos,
               ROUND(100.0 * (1 - SUM(fallido) / COUNT(*)), 2) AS tasa_exito,
               ROUND(AVG(distancia_km), 2) AS km_promedio,
               RANK() OVER (ORDER BY SUM(fallido) / COUNT(*)) AS ranking
        FROM pedidos WHERE estado IN ('ENTREGADO','FALLIDO') AND conductor_id IS NOT NULL
        GROUP BY conductor_id ORDER BY ranking""",

    "demanda_sku": """
        WITH t AS (
            SELECT sku, SUM(cantidad) AS unidades, COUNT(*) AS salidas
            FROM movimientos WHERE tipo = 'SALIDA' GROUP BY sku),
        a AS (
            SELECT *, SUM(unidades) OVER (ORDER BY unidades DESC) / SUM(unidades) OVER () AS acumulado
            FROM t)
        SELECT sku, unidades, salidas, ROUND(100 * acumulado, 2) AS acumulado_pct,
               CASE WHEN acumulado <= 0.8 THEN 'A' WHEN acumulado <= 0.95 THEN 'B' ELSE 'C' END AS clase_abc
        FROM a ORDER BY unidades DESC""",

    "demanda_mensual": """
        SELECT anio, mes, SUM(CASE WHEN tipo = 'SALIDA' THEN cantidad ELSE 0 END) AS unidades_salida,
               SUM(CASE WHEN tipo = 'ENTRADA' THEN cantidad ELSE 0 END) AS unidades_entrada
        FROM movimientos GROUP BY anio, mes ORDER BY anio, mes""",

    "clientes_riesgo": """
        SELECT cliente_id, COUNT(*) AS pedidos, SUM(fallido) AS fallidos,
               ROUND(100.0 * SUM(fallido) / COUNT(*), 2) AS tasa_fallo
        FROM pedidos WHERE estado IN ('ENTREGADO','FALLIDO')
        GROUP BY cliente_id HAVING COUNT(*) >= 100 ORDER BY tasa_fallo DESC LIMIT 20""",

    # Tiempo entre estados: LAG sobre la bitacora de cada pedido.
    "tiempos_estado": """
        WITH l AS (
            SELECT codigo, estado_nuevo, registrado_en,
                   LAG(registrado_en) OVER (PARTITION BY codigo ORDER BY registrado_en) AS anterior
            FROM eventos)
        SELECT estado_nuevo AS estado,
               COUNT(*) AS transiciones,
               ROUND(AVG((unix_timestamp(registrado_en) - unix_timestamp(anterior)) / 60), 1) AS minutos_promedio,
               ROUND(percentile_approx((unix_timestamp(registrado_en) - unix_timestamp(anterior)) / 60, 0.9), 1) AS minutos_p90
        FROM l WHERE anterior IS NOT NULL GROUP BY estado_nuevo ORDER BY minutos_promedio DESC""",
}


def _a_csv(nombre, filas, columnas):
    destino = entorno.RESULTADOS / "powerbi"
    destino.mkdir(parents=True, exist_ok=True)
    with open(destino / f"{nombre}.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(columnas)
        w.writerows(filas)


def ejecutar(spark):
    spark.read.parquet(entorno.ruta("silver", "pedidos")).createOrReplaceTempView("pedidos")
    spark.read.parquet(entorno.ruta("silver", "eventos")).createOrReplaceTempView("eventos")
    spark.read.parquet(entorno.ruta("silver", "movimientos")).createOrReplaceTempView("movimientos")

    resumen = {}
    for nombre, sql in CONSULTAS.items():
        df = spark.sql(sql)
        df.write.mode("overwrite").parquet(entorno.ruta("gold", nombre))
        filas = [list(r) for r in df.collect()]
        _a_csv(nombre, filas, df.columns)
        resumen[nombre] = {"columnas": df.columns, "filas": filas}

    totales = spark.sql("""
        SELECT COUNT(*) AS pedidos, COUNT(DISTINCT cliente_id) AS clientes, COUNT(DISTINCT conductor_id) AS conductores,
               MIN(fecha_despacho) AS desde, MAX(fecha_despacho) AS hasta,
               ROUND(100.0 * SUM(CASE WHEN estado = 'ENTREGADO' THEN 1 ELSE 0 END)
                     / SUM(CASE WHEN estado IN ('ENTREGADO','FALLIDO') THEN 1 ELSE 0 END), 2) AS tasa_exito
        FROM pedidos""").first().asDict()
    resumen["totales"] = totales
    entorno.guardar_json("gold.json", resumen)
    return resumen


if __name__ == "__main__":
    spark = entorno.crear_spark("ELT gold")
    r = ejecutar(spark)
    print("Tablas gold:", [k for k in r if k != "totales"])
    print("Totales:", r["totales"])
    spark.stop()
