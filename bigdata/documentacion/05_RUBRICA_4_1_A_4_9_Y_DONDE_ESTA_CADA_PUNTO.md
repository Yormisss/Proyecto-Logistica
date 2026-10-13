# RÚBRICA 4.1 A 4.9: EN QUÉ ARCHIVO ESTÁ CADA PUNTO DEL EXCEL, CON EJEMPLO

## Resumen

| Punto | Archivos principales | Evidencia visible |
|---|---|---|
| 4.1 Fuentes | `fuente_app.py`, `fuente_generador.py` | `muestras/` |
| 4.2 Volumen | `fuente_generador.py`, `bigdata.html` | Tarjetas de la pantalla |
| 4.3 Ingesta | `ingesta.py`, `ingesta_streaming.py` | `streaming.json`, capa `bronze` |
| 4.4 ETL/ELT | `etl.py`, `elt_gold.py` | `calidad_datos.json` |
| 4.5 Almacenamiento | `entorno.py`, `datalake/` | Carpetas por capa |
| 4.6 Procesamiento | `pipeline.py` + cada etapa | Salida de la terminal |
| 4.7 Tecnología | `bigdata.html`, `SUSTENTACION.md` | Tabla de tecnologías |
| 4.8 Análisis | `etl.py`, `elt_gold.py`, `reglas.py` | Tablas `gold` |
| 4.9 Resultados | `controllers/bigdata.py`, `ml_fallos.py`, `resultados/` | Pantalla y CSV |

---

## 4.1 Fuentes de datos identificadas

**Archivos:** `bigdata/fuente_app.py` y `bigdata/fuente_generador.py`.

Identificar una fuente es saber de dónde viene el dato, en qué formato y cómo se extrae. Hay cuatro:

| Fuente | Formato | Archivo que la produce |
|---|---|---|
| BD real de la app | CSV y JSON | `fuente_app.py` |
| ERP del cliente | CSV | `fuente_generador.py` |
| App móvil del conductor | JSON | `fuente_generador.py` |
| Almacén (WMS) | CSV | `fuente_generador.py` |

**Ejemplo:** `fuente_app.py` consulta la tabla `pedidos` y escribe una fila real con prefijo `APP-`.
`fuente_generador.py` crea filas como `SIM-00014342,...,BOGOTA,...,FALLIDO`. Ambas se ven en `bigdata/muestras/`.

## 4.2 Volumen y características de los datos definidos

**Archivos:** `bigdata/fuente_generador.py` (genera el volumen) y `app/views/admin/bigdata.html` (lo muestra).

Se trata de cuantificar cuántos datos hay y cómo son, con las 5V:
- **Volumen:** 300.223 pedidos, 882.510 eventos y 253.508 movimientos de inventario.
- **Velocidad:** cada entrega genera hasta 3 eventos desde el terreno.
- **Variedad:** tablas CSV, JSON y coordenadas geográficas.
- **Veracidad:** el ERP trae errores, que se miden en 4.4.
- **Valor:** reducir las entregas fallidas.

**Ejemplo:** en la pantalla Big Data aparecen las tarjetas "Pedidos analizados: 300.223" y
"Eventos de estado: 882.510", junto con la lista de las 5V.

## 4.3 Proceso de ingesta implementado

**Archivos:** `bigdata/ingesta.py` (batch) y `bigdata/ingesta_streaming.py` (tiempo real).

La ingesta es llevar los datos de las fuentes al data lake.
- **Batch:** lee los CSV y JSON de `raw/` y los escribe en Parquet en `bronze/`. Agrega tres columnas de
  linaje: `_fuente`, `_archivo` y `_ingestado_en`.
- **Streaming:** una hebra deja archivos de eventos en una carpeta y Spark los detecta apenas llegan.

**Ejemplo batch:** entran 306.319 pedidos a `bronze/pedidos`. Cada fila lleva `_fuente = ERP` o `APP`,
y por eso se sabe de dónde vino.
**Ejemplo streaming:** se emitieron 240 eventos, se persistieron 240 y se contaron por estado:
`ENTREGADO 95, EN_RUTA 53, ASIGNADO 51, FALLIDO 41`. Está en `resultados/streaming.json`.

## 4.4 Proceso ETL/ELT implementado

**Archivos:** `bigdata/etl.py` (ETL, `bronze` → `silver`) y `bigdata/elt_gold.py` (ELT, `silver` → `gold`).

- **ETL** limpia antes de dejar el dato disponible.
- **ELT** transforma con SQL dentro del motor, ya con los datos cargados.

Reglas de `etl.py`, con sus conteos reales (están en `resultados/calidad_datos.json`):

| Regla | Filas afectadas |
|---|---|
| Duplicados eliminados | 6.096 |
| Ciudades normalizadas | 38.871 |
| Estados en minúscula | 2.892 |
| Distancias inválidas (imputadas con la mediana, 15,8 km) | 1.471 |
| Prioridades nulas | 2.952 |
| Clientes sin nombre | 1.469 |

**Ejemplo:** el pedido `SIM-00014342` llegó con `ciudad = "BOGOTA"`; el ETL la deja en `"Bogota"`.
En `elt_gold.py`, ese mismo pedido suma un intento y un fallo a una fila de la tabla `fallos_zona_franja`.

## 4.5 Almacenamiento de datos definido

**Archivos:** `bigdata/entorno.py` (define las rutas del lake) y la carpeta `bigdata/datalake/`.

Se define dónde y cómo se guardan los datos: un **data lake en capas** en formato **Parquet**,
particionado por año y mes.

```
datalake/
  raw/     ← crudo (CSV/JSON)                       ~240 MB
  bronze/  ← Parquet + linaje                       ~55 MB
  silver/  ← limpio, particionado anio=/mes=        ~57 MB
  gold/    ← 9 tablas resumen                       KB
```

**Ejemplo:** la ruta `silver/pedidos/anio=2025/mes=3/` contiene solo los pedidos de marzo de 2025.
Una consulta de ese mes no lee el resto. Con `BIGDATA_LAKE=hdfs://...` la misma estructura se movería
a HDFS sin cambiar el código.

## 4.6 Procesamiento de datos implementado

**Archivos:** todo corre sobre Spark, orquestado por `bigdata/pipeline.py`. El procesamiento pesado está en
`etl.py`, `elt_gold.py` y `ml_fallos.py`.

Es el cómputo distribuido en sí: Spark divide los datos en particiones y las procesa en paralelo con
los núcleos del PC.

**Ejemplo:** `pipeline.py` ejecuta las seis etapas y tarda 3–5 minutos con 300 mil pedidos. En la terminal se
ve `[3 ETL (bronze -> silver)] listo en 57 s`, y las barras `Stage 36: (14 + 1) / 15` son las particiones
procesándose en paralelo.

## 4.7 Tecnología Big Data utilizada y justificada

**Archivos:** `app/views/admin/bigdata.html` (tabla de tecnologías), `bigdata/SUSTENTACION.md`
(justificación completa) y `bigdata/requirements.txt`.

| Tecnología | Por qué |
|---|---|
| Spark / PySpark | Procesa en paralelo lo que una consulta de base de datos no maneja bien |
| Spark SQL | Funciones de ventana sobre millones de filas |
| Structured Streaming | Mismo código que batch; se cambia a Kafka sin reescribir |
| MLlib | El entrenamiento escala con el clúster |
| Parquet | Columnar, comprimido y con esquema |

**Ejemplo de justificación:** "Usamos Parquet y no CSV porque una consulta lee solo las columnas que
necesita y los tipos vienen guardados." Eso explica por qué `silver/pedidos` pesa 22 MB cuando el CSV
original pesa 55 MB.

## 4.8 Análisis y transformación de datos

**Archivos:** `bigdata/etl.py` (variables derivadas), `bigdata/elt_gold.py` (análisis con SQL) y
`bigdata/reglas.py` (reglas de zona y franja).

Transformar es convertir datos limpios en variables útiles. Analizar es responder preguntas con ellos.

- **Transformación, `etl.py`:** las coordenadas se convierten en `zona`, la hora en `franja` y la fecha
  en `dia_semana`.
- **Análisis, `elt_gold.py`:** nueve consultas SQL, entre ellas `SUM() OVER (ORDER BY ...)` para la
  clasificación ABC, `LAG() OVER (PARTITION BY pedido)` para el tiempo entre estados y `RANK()` para el
  ranking de conductores.

**Ejemplo:** la consulta de `demanda_sku` da `SKU-031: 9.545 unidades, clase A`. Con `LAG` se calcula
cuánto pasa entre `ASIGNADO` y `EN_RUTA` de cada pedido, y de ahí salen el promedio y el percentil 90.

## 4.9 Resultados obtenidos a partir de los datos

**Archivos:** `app/controllers/bigdata.py`, `app/views/admin/bigdata.html`, `bigdata/ml_fallos.py` y
`bigdata/resultados/`.

Es lo que el negocio ve y puede usar:
- **Fallos por zona y franja:** Occidente de noche falla el **29,5 %**, frente a un 17 % general.
- **Modelo predictivo:** la zona es la variable más influyente (~55 %); detecta ~70 % de los fallos
  reales. AUC 0,62.
- **Producto de mayor salida:** `SKU-031`. La clasificación ABC muestra qué productos concentran el 80 %
  de la demanda.
- **Conductores:** ranking de mejores y peores por tasa de éxito.

**Dónde verlo:** en la app, menú **Big Data**, sección 4.9. Los mismos datos quedan en
`resultados/powerbi/*.csv` para el dashboard.

---

## Dos advertencias para la sustentación

- **4.5 y 4.7:** no hay clúster HDFS desplegado. Se usa Parquet, el formato nativo de Hadoop, y el lake pasa
  a HDFS cambiando una variable.
- **4.9:** el modelo es modesto (AUC 0,62) y los datos son simulados. Los resultados demuestran el
  pipeline, no una operación real.
