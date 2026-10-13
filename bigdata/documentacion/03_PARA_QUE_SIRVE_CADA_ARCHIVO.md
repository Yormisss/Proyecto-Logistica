# PARA QUÉ SIRVE CADA ARCHIVO DE LA CARPETA `bigdata/`

## Código del pipeline (en el orden en que se ejecuta)

| Archivo | Para qué sirve |
|---|---|
| `pipeline.py` | **Es el que se ejecuta.** Llama a todos los demás en orden y muestra el tiempo de cada etapa |
| `entorno.py` | Configuración común: crea la sesión de Spark, define dónde está el data lake y cómo guardar resultados. Todos los demás lo importan |
| `reglas.py` | Reglas de negocio compartidas: cómo se calcula la **zona** (Norte, Sur...) y la **franja horaria**. Están en un solo sitio para que el generador y el ETL usen exactamente la misma definición |
| `fuente_app.py` | Fuente 1: lee la base de datos real de la app (SQLite o MySQL) y exporta los pedidos y eventos a CSV/JSON |
| `fuente_generador.py` | Fuentes 2, 3 y 4: genera los datos simulados (ERP, app móvil y almacén), con errores a propósito |
| `ingesta.py` | Pasa los archivos crudos (`raw`) a Parquet (`bronze`), anotando de qué fuente y archivo vino cada fila |
| `etl.py` | Limpia (`bronze` → `silver`): quita duplicados, corrige ciudades, estados y distancias, y calcula zona y franja. Cuenta cuántas filas corrigió cada regla |
| `elt_gold.py` | Construye con Spark SQL las 9 tablas resumen (`silver` → `gold`) y las exporta a CSV para Power BI |
| `ml_fallos.py` | Entrena el modelo que estima la probabilidad de que una entrega falle |
| `ingesta_streaming.py` | Demostración de tiempo real: simula celulares enviando eventos y los procesa al llegar |

Cada etapa también se puede ejecutar sola: `python bigdata/etl.py`, `python bigdata/elt_gold.py`, etc.

## Documentación y datos

| Archivo o carpeta | Para qué sirve | ¿Va a GitHub? |
|---|---|---|
| `README.md` | Resumen técnico: diagrama, comandos y mapa de la rúbrica | Sí |
| `SUSTENTACION.md` | Qué se hizo, por qué y por qué así y no de otra forma | Sí |
| `INSTALACION_COMPANEROS.md` | Pasos para cargarlo en otro PC | Sí |
| `documentacion/` | Esta carpeta | Sí |
| `requirements.txt` | Lo que hay que instalar con `pip`: `pyspark` y `numpy` | Sí |
| `muestras/` | 2.000 filas de cada fuente, para mostrar los datos | Sí |
| `resultados/` | JSON y CSV que produce el pipeline; la pantalla de la app los lee y Power BI los usa | Sí |
| `datalake/` | Los datos completos por capas (más de 300 MB). Se crea al ejecutar el pipeline | **No** (se regenera) |

## Resultados que produce el pipeline (`resultados/`)

| Archivo | Contenido |
|---|---|
| `calidad_datos.json` | Cuántas filas corrigió cada regla del ETL |
| `gold.json` | Las 9 tablas resumen y los totales, para la pantalla |
| `ml_fallos.json` | Métricas e importancia de variables del modelo |
| `streaming.json` | Eventos emitidos, persistidos y conteo por estado y ventana |
| `powerbi/*.csv` | Las 9 tablas resumen en CSV para Power BI |

## Fuera de `bigdata/`: lo único que se toca de la app

| Archivo | Cambio |
|---|---|
| `app/controllers/bigdata.py` | Controlador nuevo: lee los resultados y los pasa a la vista |
| `app/views/admin/bigdata.html` | Vista nueva: la pantalla Big Data |
| `app/__init__.py` | +2 líneas: registrar el controlador |
| `app/views/base.html` | +2 líneas: enlace "Big Data" en el menú |
| `.gitignore` | Ignora `bigdata/datalake/` |

No se modificó nada del asistente de voz (Retell), de las automatizaciones (Make) ni de los modelos
de la base de datos.
