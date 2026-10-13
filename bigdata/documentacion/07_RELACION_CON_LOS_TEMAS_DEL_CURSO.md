# RELACIÓN CON LOS TEMAS DEL CURSO (SEMANAS 1 A 10) Y QUÉ FALTA

## Primer corte (semanas 1 a 5)

| Sem. | Tema | Dónde se ve en el proyecto | Cobertura |
|---|---|---|---|
| 1 | Introducción al Big Data (5V, aplicaciones) | Las 5V en la pantalla y en `01_QUE_ES...`; caso: entregas fallidas | Completa |
| 2 | Arquitecturas Batch, Streaming, Lambda y Kappa | Lambda: batch (`ingesta.py`, `etl.py`) + velocidad (`ingesta_streaming.py`) | Completa |
| 3 | Ecosistema Hadoop (HDFS, YARN, MapReduce) | Lake en Parquet compatible con HDFS; Spark sustituye a MapReduce | **Parcial:** no hay clúster HDFS |
| 4 | Apache Spark, Anaconda, Jupyter | Pipeline en Spark local | Completa, **sin notebook** |
| 5 | PySpark: RDD, DataFrames, transformaciones, acciones | Todo el código usa DataFrames; ver transformaciones/acciones en `SUSTENTACION.md` | Completa (DataFrames; sin RDD explícitos) |

## Segundo corte (semanas 6 a 10)

| Sem. | Tema | Dónde se ve en el proyecto | Cobertura |
|---|---|---|---|
| 6 | Spark SQL, consultas distribuidas, optimización | `elt_gold.py`: 9 consultas, funciones de ventana, particionado y caché | Completa |
| 7 | Integración de datos: CSV, JSON, XML, Parquet; ETL | `etl.py`, `ingesta.py`; entran CSV y JSON y salen Parquet | Completa, **sin XML** |
| 8 | Bases de datos NoSQL (MongoDB, Cassandra, Redis) | — | **No cubierto** |
| 9 | Data Warehouse y Streaming (Hive, Data Lake, Kafka, Spark Streaming) | Data lake, tablas `gold` como almacén y Structured Streaming | **Parcial:** sin Hive ni Kafka |
| 10 | Visualización con Power BI | Pantalla en la app y CSV en `resultados/powerbi/` | **Dashboard de Power BI pendiente** |

## Cómo se justifica lo que falta

- **Hadoop (sem. 3):** se usa Parquet, el formato nativo del ecosistema, y las librerías cliente de Hadoop que
  trae Spark. Cambiando `BIGDATA_LAKE=hdfs://...` el lake pasa a HDFS sin tocar el código. Instalar un
  clúster HDFS real es un laboratorio aparte.
- **NoSQL (sem. 8):** no se implementó. El caso natural sería guardar en **MongoDB** los eventos JSON de la
  app, que son semiestructurados y cambian de forma.
- **Hive y Kafka (sem. 9):** el almacén se hizo con Spark SQL sobre Parquet (misma idea: SQL sobre
  almacenamiento distribuido, pero sin metastore de Hive). El streaming lee de una carpeta; para Kafka solo
  se cambia la fuente del stream (`format("kafka")`). Ninguno se probó.
- **Power BI (sem. 10):** los CSV están listos en `resultados/powerbi/`; falta armar el dashboard.

## Qué se podría agregar si el profesor lo exige

| Pendiente | Esfuerzo | Valor |
|---|---|---|
| Dashboard de Power BI | Bajo (los CSV ya existen) | Cierra la semana 10 |
| Guardar eventos en MongoDB | Medio (Docker + un script) | Cubre la semana 8 con sentido de negocio |
| Notebook de Jupyter | Bajo | Cubre la semana 4 |
| Kafka real | Medio-alto (broker en Docker) | Cubre la semana 9 |
| HDFS real | Alto | Cubre la semana 3 |
