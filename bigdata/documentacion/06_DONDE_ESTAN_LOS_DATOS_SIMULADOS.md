# DÓNDE ESTÁN LOS DATOS SIMULADOS Y QUÉ MOSTRAR SI EL PROFESOR LOS PIDE

## Cómo se crean

No salen de ningún archivo oculto: **los crea el pipeline** al ejecutarse, con `bigdata/fuente_generador.py`.
Se generan con Spark (sin bucles de Python), con una semilla fija (42), así que **siempre salen los mismos datos**.

## Dónde están

Todo queda en `bigdata/datalake/`, **que no se sube a GitHub** porque pesa más de 300 MB.

| Carpeta | Contenido | Tamaño aprox. |
|---|---|---|
| `datalake/raw/erp_pedidos/` | Pedidos del ERP (CSV) | 55 MB |
| `datalake/raw/app_eventos_sim/` | Eventos de la app móvil (JSON) | 170 MB |
| `datalake/raw/wms_movimientos/` | Movimientos del almacén (CSV) | 15 MB |
| `datalake/raw/app_pedidos/` y `raw/app_eventos/` | Datos **reales** exportados de la app | < 1 MB |
| `datalake/bronze/` y `silver/` | Los mismos datos en Parquet (ingeridos y limpios) | 22–29 MB c/u |
| `datalake/gold/` | Las 9 tablas analíticas | pocos KB |

## Qué mostrar si el profesor los pide

| Opción | Cuándo | Dónde |
|---|---|---|
| **Muestra liviana** | Para abrir en Excel o el editor | `bigdata/muestras/` (2.000 filas por fuente). **Sí está en el repositorio** |
| **Volumen completo** | Para mostrar que son cientos de miles de filas | `bigdata/datalake/raw/` en el PC donde se ejecutó el pipeline |
| **Regenerarlos en vivo** | Para demostrar que el pipeline funciona | `python bigdata/pipeline.py` (3–5 min) |

## Qué hay en `bigdata/muestras/`

| Archivo | Contenido |
|---|---|
| `erp_pedidos_muestra.csv` | 2.000 pedidos simulados del ERP, incluidos los defectos (`BOGOTA`, ` bogota `, duplicados) |
| `app_eventos_muestra.json` | 2.000 eventos de cambio de estado (una línea JSON por evento) |
| `wms_movimientos_muestra.csv` | 2.000 movimientos de inventario |
| `app_pedidos_reales.csv` | Los 223 pedidos **reales** de la app |

## Lo que se debe decir con claridad

La app real solo trae unos 220 pedidos de demostración; con eso Spark no tiene sentido (una consulta
normal alcanza). Por eso se simularon 300 mil, con el mismo esquema de la app y con errores típicos de un
ERP: duplicados (2 %), ciudades escritas distinto, nulos, distancias negativas y estados en minúscula.
**Los resultados reflejan las reglas con que se simuló: demuestran el pipeline, no describen una
operación real.** Los 223 pedidos reales entran al mismo pipeline como una fuente más.
