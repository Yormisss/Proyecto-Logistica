# Guía de sustentación — Módulo de Big Data

Cada paso responde tres preguntas: **qué se hizo**, **por qué** y **por qué así y no de otra forma**.
Al final está la relación con las 10 semanas del curso y las preguntas que probablemente harán.

---

## 0. El problema que justifica Big Data

La app registra, por cada pedido: el pedido, entre 1 y 3 eventos de estado y los movimientos de
inventario. En una flota real son millones de filas al año, llegan en tiempo real desde los
celulares, vienen de sistemas distintos (app, ERP, almacén) y con errores. La pregunta de negocio
es: **¿qué entregas fallan, dónde, a qué hora y por qué?** Cada entrega fallida es un viaje
perdido.

**Decisión:** no tocar la app. Sigue siendo el sistema transaccional (OLTP). Big Data es una capa
analítica (OLAP) al lado, que lee lo que la app genera.
**Por qué así:** mezclar consultas analíticas pesadas con la operación diaria ralentiza a los
conductores (el RNF2 exige respuestas de 3–5 s). Separar OLTP de OLAP es la práctica estándar.

## 1. Fuentes de datos (4.1)

| Fuente | Qué es | Formato | Archivo |
|---|---|---|---|
| App | BD real de la app: 223 pedidos y su bitácora | CSV / JSON | `fuente_app.py` |
| ERP | Histórico de pedidos del cliente | CSV | `fuente_generador.py` |
| App móvil | Eventos de estado del conductor | JSON | `fuente_generador.py` |
| WMS | Movimientos del almacén | CSV | `fuente_generador.py` |

**Por qué datos simulados:** la app solo trae ~220 pedidos de demostración; con eso Spark no tiene
sentido (una consulta SQL normal basta). Se generaron 300 mil pedidos con Spark mismo (`spark.range`
y funciones aleatorias, sin bucles de Python) para trabajar con un volumen que justifique la
herramienta. Los 223 reales entran al pipeline como una fuente más.
**Por qué defectos a propósito:** duplicados (2 %), ciudades escritas distinto, nulos, distancias
negativas y estados en minúscula. Un ETL sin datos sucios no demuestra nada.
**Se debe decir con claridad:** los resultados reflejan las reglas con que se simuló; demuestran el
pipeline, no una operación real.

## 2. Volumen y características — las 5V (4.2)

Volumen (300 mil pedidos, 880 mil eventos, 250 mil movimientos), Velocidad (eventos desde el
terreno), Variedad (tablas, JSON, coordenadas), Veracidad (errores del ERP), Valor (reducir
entregas fallidas). Los números salen en la pantalla **Big Data** de la app.

## 3. Arquitectura: Lambda (semana 2)

```
Batch:      raw ─► bronze ─► silver ─► gold ─► análisis        (histórico)
Velocidad:  eventos en vivo ─► Structured Streaming ─► bronze  (tiempo real)
```

**Por qué Lambda y no Kappa:** Kappa trata todo como stream y reprocesa reproduciendo el log; exige
un broker como Kafka con retención larga. Aquí hay un histórico grande que se reprocesa por lotes y
una parte pequeña que necesita inmediatez, que es exactamente el caso de Lambda. Además no se
necesita desplegar infraestructura adicional.

## 4. Data lake en capas y Parquet (semanas 3 y 7)

| Capa | Contenido | Por qué existe |
|---|---|---|
| raw | Archivos tal como llegan (CSV/JSON) | Fuente de verdad: nunca se modifica |
| bronze | Lo mismo en Parquet + metadatos (fuente, archivo, fecha de ingesta) | Linaje: saber de dónde vino cada fila |
| silver | Limpio, tipado, sin duplicados | Lo que se puede analizar con confianza |
| gold | Tablas agregadas para consumo | Lo que ven la pantalla y Power BI |

**Por qué capas:** si un error se descubre en el ETL, se reprocesa desde raw sin volver a pedirle
los datos a las fuentes. Es la arquitectura "medallion".
**Por qué Parquet y no CSV:** es columnar (una consulta lee solo las columnas que necesita),
comprime (el CSV de 55 MB baja considerablemente), guarda el esquema y los tipos, y permite
particionar por año/mes para leer solo el periodo consultado.
**Relación con Hadoop:** Parquet es el formato nativo del ecosistema Hadoop/Spark, y la ruta del
lake se cambia a HDFS con una variable (`BIGDATA_LAKE=hdfs://...`) sin tocar el código. **No se
desplegó un clúster HDFS**: Spark local usa las librerías cliente de Hadoop (por eso en Windows
pide `winutils`).

## 5. Ingesta (4.3)

**Batch (`ingesta.py`):** lee cada fuente y la escribe en bronze. Los CSV entran *como texto* a
propósito: la ingesta no decide tipos ni corrige; eso es trabajo del ETL. Así bronze nunca pierde
información por una conversión fallida.
**Streaming (`ingesta_streaming.py`):** simula celulares que reportan cambios de estado (6 lotes de
40 eventos). Spark detecta los archivos nuevos, los guarda y cuenta eventos por estado en ventanas
de 30 s.
**Por qué una carpeta y no Kafka:** Kafka requiere un broker corriendo. El código está escrito para
que cambiar a Kafka sea reemplazar la fuente del stream (`format("kafka")`); el resto del procesamiento
es idéntico, y eso es justamente lo que ofrece Structured Streaming. **Kafka no se probó.**

## 6. ETL (4.4) — `etl.py`

Reglas aplicadas, cada una con su conteo (ver `resultados/calidad_datos.json` y la pantalla):
1. Descartar filas sin código (no identificables).
2. Eliminar duplicados (conserva la última ingesta): ~6 mil.
3. Tipar columnas (fechas, enteros, decimales).
4. Normalizar ciudad (sin acentos, mayúsculas ni "D.C."): ~39 mil.
5. Estados a mayúscula; distancias ≤ 0 reemplazadas por la **mediana** (no por el promedio, que
   distorsionan los valores extremos); prioridad nula → 3; nombre nulo → derivado del id.
6. Variables derivadas: zona, franja horaria, día de la semana, "¿dentro de la ventana?".

**ETL vs ELT:** `etl.py` transforma *antes* de guardar en silver (Extract-Transform-Load). `elt_gold.py`
parte de silver ya cargado y hace la transformación final con SQL dentro del motor (ELT). Se usan
los dos para mostrar la diferencia: el ETL es para limpiar; el ELT, para agregar con la potencia
del motor y en el lenguaje que conocen los analistas.

## 7. Procesamiento con Spark (semanas 4 a 6)

**Por qué Spark y no pandas o MySQL:** pandas carga todo en la memoria de un solo proceso; una base
relacional ejecuta la consulta en un servidor. Spark divide los datos en particiones y las procesa
en paralelo (aquí, los núcleos del PC; en producción, un clúster) con el mismo código.
**Por qué Spark y no MapReduce (semana 3):** MapReduce escribe a disco entre cada etapa; Spark
mantiene los datos en memoria y expresa los cálculos como DataFrames. Es el sucesor natural.

**Conceptos del curso visibles en el código:**
- *Transformaciones* (perezosas): `withColumn`, `filter`, `groupBy`, `join`: solo construyen un plan.
- *Acciones* (ejecutan): `count`, `collect`, `write`. Hasta ahí no se procesa nada.
- *Shuffle*: `groupBy`, `row_number() over(partition by)` y los `JOIN` redistribuyen datos entre
  particiones; es lo más costoso, y por eso `spark.sql.shuffle.partitions` se ajusta a 8 en vez del
  valor por defecto (200) para un equipo local.
- *Caché*: `.cache()` evita recalcular el DataFrame que se usa varias veces (limpieza de pedidos).

## 8. Spark SQL y almacén de datos (semanas 6 y 9) — `elt_gold.py`

Nueve consultas producen las tablas gold. Las más interesantes técnicamente:
- **`SUM(...) OVER (ORDER BY ...)`** en `demanda_sku`: acumulado para la **clasificación ABC** (80 %
  de la demanda = clase A).
- **`LAG(...) OVER (PARTITION BY codigo ORDER BY registrado_en)`** en `tiempos_estado`: tiempo entre
  un estado y el anterior de cada pedido, más percentil 90.
- **`RANK()`** en `rendimiento_conductor`.

**Por qué esto es un "data warehouse":** son tablas de hechos agregadas, orientadas a consulta,
separadas del sistema transaccional. **No se usó Hive**: el motor SQL es Spark SQL leyendo Parquet.
Es la misma idea (SQL sobre almacenamiento distribuido), pero sin metastore de Hive.

## 9. Modelo predictivo (resultado de valor) — `ml_fallos.py`

Random Forest de Spark MLlib que estima la probabilidad de que una entrega falle.
- **Solo variables conocidas antes de despachar** (zona, hora planeada, prioridad, distancia,
  conductor, etc.). Se excluye la hora real de entrega porque es consecuencia del resultado:
  incluirla sería *fuga de datos* y daría un modelo bueno solo en apariencia.
- **Tasa de fallo del conductor** calculada *solo con el set de entrenamiento*, para que la prueba no
  vea sus propias respuestas.
- **Peso por clase:** los fallos son ~17 %; sin ponderar, el modelo aprendería a decir "entrega" siempre.
- **Resultado honesto:** AUC ≈ 0,62; detecta ~70 % de los fallos reales con muchos falsos positivos.
  El fallo en los datos depende de azar que ninguna variable explica. Sirve para **priorizar
  seguimiento**, no para descartar pedidos. Lo importante: identifica la **zona** como factor principal,
  que coincide con la regla usada al simular los datos.

## 10. Resultados en la app (4.9)

`app/controllers/bigdata.py` **lee los JSON** que deja el pipeline y la pantalla `/admin/bigdata` los
muestra. **Por qué no ejecuta Spark en la petición web:** arrancar Spark tarda segundos y consume
gigas de memoria; bloquearía el servidor. Se desacopla: el pipeline se ejecuta por separado (o se
programa) y la web solo consume resultados, igual que Power BI. Los mismos datos salen en CSV
en `resultados/powerbi/`.

---

## Relación con los temas del curso

| Sem. | Tema | Dónde se ve en el proyecto | Cobertura |
|---|---|---|---|
| 1 | Intro, 5V, casos | Sección 0 y 2; pantalla Big Data | Completa |
| 2 | Arquitecturas | Lambda (sección 3): batch + streaming | Completa |
| 3 | Hadoop (HDFS, YARN, MapReduce) | Lake en Parquet compatible con HDFS; Spark sustituye a MapReduce | **Parcial**: no hay clúster HDFS |
| 4 | Spark, Jupyter | Pipeline en Spark local | Completa (sin notebook) |
| 5 | PySpark | RDD/DataFrames, transformaciones y acciones | Completa (DataFrames) |
| 6 | Spark SQL | `elt_gold.py` | Completa |
| 7 | ETL, CSV/JSON/Parquet | `etl.py`, formatos de entrada y salida | Completa (sin XML) |
| 8 | NoSQL | — | **No cubierto** |
| 9 | Data Warehouse y Streaming | Tablas gold + Structured Streaming | **Parcial**: sin Hive ni Kafka |
| 10 | Visualización | Pantalla de la app + CSV para Power BI | Dashboard de Power BI pendiente |

## Preguntas probables y respuestas

- **¿Por qué datos simulados?** La app no tiene volumen; se generó con Spark. Los resultados
  demuestran el pipeline, no la operación real.
- **¿Por qué el modelo tiene AUC 0,62?** El fallo tiene componente aleatorio; el modelo prioriza
  riesgo, no lo determina. Está evaluado en datos que no vio al entrenar.
- **¿Dónde está Hadoop?** Se usa su formato (Parquet) y librerías; no hay clúster HDFS. El lake se
  mueve a HDFS cambiando una variable.
- **¿Dónde está Kafka/Hive/MongoDB?** No se desplegaron. Streaming y SQL se hicieron con Spark.
  Los eventos JSON de la app serían el caso natural para MongoDB.
- **¿Escala?** El mismo código corre en un clúster cambiando `SPARK_MASTER` y `BIGDATA_LAKE`.
  Se probó con 300 mil pedidos en un PC.
- **¿Qué es un DataFrame y por qué es perezoso?** Una tabla distribuida; Spark acumula las
  transformaciones en un plan y lo optimiza antes de ejecutarlo al llegar una acción.

---

## Dónde están los datos simulados

Los datos se generan al ejecutar `python bigdata/pipeline.py`, en `bigdata/datalake/`. **Esa carpeta
no se sube a GitHub** (pesa más de 300 MB); si el profesor los pide hay tres opciones:

| Qué mostrar | Dónde |
|---|---|
| Una muestra para abrir en Excel o el editor | `bigdata/muestras/` (2.000 filas por fuente, sí está en el repositorio) |
| El volumen completo, ya generado | `bigdata/datalake/raw/` en el PC donde se corrió el pipeline |
| Regenerarlo delante del profesor | `python bigdata/pipeline.py` (3–5 min); la semilla fija (42) produce siempre los mismos datos |

| Carpeta del lake | Contenido | Tamaño aprox. |
|---|---|---|
| `raw/erp_pedidos/` | Pedidos del ERP (CSV) | 55 MB |
| `raw/app_eventos_sim/` | Eventos de la app móvil (JSON) | 170 MB |
| `raw/wms_movimientos/` | Movimientos del almacén (CSV) | 15 MB |
| `raw/app_pedidos/`, `raw/app_eventos/` | Datos reales exportados de la app | < 1 MB |
| `bronze/`, `silver/` | Los mismos datos en Parquet (ingeridos y limpios) | 22–29 MB c/u |
| `gold/` | Las 9 tablas analíticas | pocos KB |
| `../resultados/` | JSON y CSV para la pantalla y Power BI (sí están en el repositorio) | 59 KB |
