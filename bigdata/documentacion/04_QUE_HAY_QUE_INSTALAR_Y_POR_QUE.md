# QUÉ HAY QUE INSTALAR PARA EJECUTAR EL MÓDULO Y POR QUÉ

## Lo necesario

| Qué | Por qué | Cuándo |
|---|---|---|
| **Java 17 o superior** | Spark está escrito en Scala y corre sobre la máquina virtual de Java. Sin Java no arranca. Se probó con Java 24 | Siempre |
| **Python 3.10+** | PySpark es la interfaz de Python para Spark. El equipo ya lo tiene por la app | Siempre |
| **`pyspark`** (con `pip`) | Trae el propio Spark, así que **no hay que instalar Spark aparte** | Siempre |
| **`numpy`** (con `pip`) | Lo exige MLlib, el módulo de aprendizaje automático, para sus cálculos numéricos. Sin él falla `ml_fallos.py` | Siempre |
| **`winutils.exe` y `hadoop.dll`** | Spark usa librerías de Hadoop para escribir archivos, y en Windows necesitan estos dos ayudantes. En Linux o macOS no hacen falta | Solo en Windows |

Con el entorno virtual de la app activado:

```
pip install -r bigdata/requirements.txt
```

Eso instala `pyspark` y `numpy` sin tocar el `requirements.txt` de la app, así que no afecta el
despliegue en Render.

### Detalle de `winutils` en Windows

1. Descargue `winutils.exe` y `hadoop.dll` de `https://github.com/cdarlint/winutils`, carpeta `hadoop-3.3.6/bin`.
2. Guárdelos en `C:\hadoop\bin` (o en `<carpeta del proyecto>\..\hadoop\bin`, que es donde los busca por defecto).
3. Defina la variable de entorno `HADOOP_HOME=C:\hadoop` y abra una terminal nueva.

## Lo que ya tiene la app y el pipeline reutiliza

Flask, SQLAlchemy y PyMySQL: los usa `fuente_app.py` para leer la base de datos de la app.

## Lo que NO hay que instalar

| Qué | Por qué no |
|---|---|
| **Docker** | Solo lo necesita la app si su MySQL va en Docker. El pipeline no lo usa |
| **Hadoop, Hive, Kafka, MongoDB, Redis** | No se desplegaron. Las partes de Hadoop que Spark necesita vienen dentro de `pyspark` |
| **Power BI Desktop** | Solo hace falta para armar el dashboard de la semana 10. El pipeline y la pantalla funcionan sin él |
| **Claves de Retell o Make** | El módulo no las usa y no toca el agente de IA |

## Una aclaración para la sustentación

`pip install pyspark` instala Spark en modo **local**, que usa los núcleos del PC. Para un clúster real
habría que instalar y configurar Spark en varias máquinas, pero **el código sería el mismo**: solo cambian
las variables `SPARK_MASTER` y `BIGDATA_LAKE`.

## Cómo ejecutarlo

```
python bigdata/pipeline.py                      # 300.000 pedidos simulados, ~3,5 min
python bigdata/pipeline.py --pedidos 1000000    # más volumen
python bigdata/pipeline.py --sin-generacion     # reprocesa el raw que ya existe
```

Después, con la app corriendo, abra **Big Data** en el menú (`/admin/bigdata`).

## Problemas frecuentes

| Síntoma | Causa y solución |
|---|---|
| `HADOOP_HOME and hadoop.home.dir are unset` (Windows) | Falta `winutils`; defina `HADOOP_HOME` y abra una terminal nueva |
| `JAVA_HOME is not set` / `java not found` | Instale Java 17+ y reinicie la terminal |
| `PYTHON_VERSION_MISMATCH` | Spark usa otro Python; ejecute siempre con el Python del entorno virtual |
| `ModuleNotFoundError: pyspark` o `numpy` | Falta `pip install -r bigdata/requirements.txt` en ese entorno virtual |
| La pantalla dice "Aún no hay resultados" | Falta ejecutar el pipeline, o el repositorio no trajo `bigdata/resultados/` |
| `OutOfMemoryError` | Defina `SPARK_MEMORIA=2g`, o use `--pedidos 100000` |
| Error de conexión a MySQL al exportar | El contenedor Docker de la app debe estar arriba: `docker compose up -d` |
