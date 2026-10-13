# Módulo de Big Data

Pipeline de Apache Spark sobre los datos de la operación logística. La app sigue siendo el
sistema transaccional (OLTP); este módulo toma lo que ella genera, lo procesa a escala y
devuelve resultados que se ven en **Admin → Big Data** (`/admin/bigdata`) y en Power BI.

```
 FUENTES                INGESTA            ETL / ELT           ANÁLISIS             SALIDA
 App (SQLite/MySQL) ┐                                      ┌ Spark SQL (9 tablas) ┐ Pantalla "Big Data"
 ERP (CSV)          ├─► raw ─► bronze ─► silver ─► gold ──┼ MLlib (fallos)       ├ CSV para Power BI
 App móvil (JSON)   │  CSV/JSON  Parquet  limpio   SQL    └ Streaming (ventanas) ┘ JSON de resultados
 WMS almacén (CSV)  ┘
```

## Puesta en marcha

```bash
.venv\Scripts\python -m pip install -r bigdata\requirements.txt
.venv\Scripts\python bigdata\pipeline.py                    # 300.000 pedidos, ~3,5 min
.venv\Scripts\python bigdata\pipeline.py --pedidos 1000000  # más volumen
.venv\Scripts\python bigdata\pipeline.py --sin-generacion   # reprocesa el raw existente
```

Requisitos: Java 17+ (probado con 24) y Python 3.10+. **En Windows**, Spark necesita
`winutils.exe` y `hadoop.dll` (parte de Hadoop): colóquelos en `<carpeta>\bin` y defina
`HADOOP_HOME=<carpeta>`; por defecto se busca `..\hadoop` junto al proyecto. En Linux/macOS
no hace falta.

Cada etapa se puede correr sola: `fuente_generador.py`, `ingesta.py`, `etl.py`,
`elt_gold.py`, `ml_fallos.py`, `ingesta_streaming.py`.

## Mapa de la rúbrica

| # | Criterio | Dónde está |
|---|---|---|
| 4.1 | Fuentes de datos identificadas | `fuente_app.py` (BD de la app), `fuente_generador.py` (ERP, app móvil, WMS) |
| 4.2 | Volumen y características | Pantalla Big Data (5V y conteos); 300 mil pedidos, 880 mil eventos, 250 mil movimientos |
| 4.3 | Proceso de ingesta | `ingesta.py` (batch, con linaje) e `ingesta_streaming.py` (Structured Streaming) |
| 4.4 | Proceso ETL/ELT | `etl.py` (ETL a silver, con conteo por regla) y `elt_gold.py` (ELT con Spark SQL) |
| 4.5 | Almacenamiento definido | Data lake en capas raw/bronze/silver/gold, Parquet particionado por año y mes |
| 4.6 | Procesamiento implementado | Spark: joins, funciones de ventana, agregaciones, MLlib, streaming |
| 4.7 | Tecnología justificada | Tabla en la pantalla Big Data y sección siguiente |
| 4.8 | Análisis y transformación | `elt_gold.py` (9 tablas) y variables derivadas en `etl.py` |
| 4.9 | Resultados | Pantalla Big Data, `resultados/*.json`, `resultados/powerbi/*.csv` |

## Decisiones y justificación

- **Por qué Big Data en este proyecto.** Cada pedido deja un pedido, 1–3 eventos de estado y
  movimientos de inventario. Con una flota real son millones de filas al año, llegan en tiempo
  real desde el terreno, vienen en formatos distintos y con errores. La pregunta que vale
  dinero —¿qué entregas van a fallar y por qué?— exige cruzarlas todas.
- **Datos simulados.** La app solo trae ~220 pedidos de demostración, que no justifican Spark.
  Por eso `fuente_generador.py` produce dos años de operación con el mismo esquema y defectos
  típicos de un ERP (duplicados, ciudades mal escritas, nulos, valores negativos). Los 223
  pedidos reales de la app entran al mismo pipeline como una fuente más. **Los resultados
  analíticos reflejan las reglas con que se simuló, no una operación real**; sirven para
  demostrar el pipeline, no para tomar decisiones de negocio.
- **Arquitectura tipo Lambda.** Capa batch (ingesta + ETL + gold) para el histórico y capa de
  velocidad (Structured Streaming) para los eventos en vivo.
- **Spark local.** Corre en `local[*]` en un solo equipo. El mismo código corre en un clúster
  cambiando `SPARK_MASTER`, y sobre HDFS cambiando `BIGDATA_LAKE=hdfs://...`.
- **Streaming con carpeta, no Kafka.** El stream lee de una carpeta para no exigir un broker.
  En producción se reemplaza por Kafka cambiando solo la fuente (ver `ingesta_streaming.py`).
- **Modelo predictivo.** Random Forest de MLlib sobre variables conocidas *antes* de despachar.
  Se excluye la hora real de entrega porque revela el resultado. AUC ≈ 0,62: el fallo depende
  de factores aleatorios que el modelo no ve, así que sirve para priorizar seguimiento (detecta
  ~70 % de los fallos reales), no para descartar pedidos.

## Power BI

`Obtener datos → Texto/CSV` y cargue los archivos de `bigdata/resultados/powerbi/`
(`kpi_mensual`, `fallos_zona_franja`, `rendimiento_conductor`, `demanda_sku`, etc.). Para
apuntar directo al lake use el conector Parquet sobre `bigdata/datalake/gold/`.

## Qué no incluye

Hadoop/HDFS, Hive, Kafka, MongoDB y Redis no están desplegados. HDFS y Kafka están
contemplados en el código (cambio de URI y de fuente del stream), pero no se probaron aquí.
