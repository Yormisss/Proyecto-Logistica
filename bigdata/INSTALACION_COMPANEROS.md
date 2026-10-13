# Cómo cargar el módulo de Big Data en la app existente

Para quien ya tiene la app corriendo (con Make, Retell y MySQL en Docker). El módulo **no
modifica** el asistente de voz, las automatizaciones ni la base de datos: solo **lee** la BD para
exportar los pedidos y agrega una pantalla nueva. No necesita claves de Retell ni de Make.

## Qué cambia en el repositorio

| Archivo | Cambio |
|---|---|
| `bigdata/` | Carpeta nueva con el pipeline de Spark, documentación y resultados |
| `app/controllers/bigdata.py` | Controlador nuevo (pantalla de resultados) |
| `app/views/admin/bigdata.html` | Vista nueva |
| `app/__init__.py` | +2 líneas: registrar el controlador |
| `app/views/base.html` | +2 líneas: enlace "Big Data" en el menú |
| `.gitignore` | Ignora `bigdata/datalake/` (cientos de MB, se regenera) |

## Pasos

### 1. Traer los cambios
```bash
git pull origin main        # o la rama donde se subió
```
Si ya hay cambios locales sin guardar, hágase `git stash` antes y `git stash pop` después.

### 2. Requisitos (una sola vez)
- **Java 17 o superior** (`java -version`). Si no está, instale Temurin JDK 17 o 21.
- **Python 3.10+** (el mismo del entorno virtual de la app).
- **Solo en Windows:** Spark necesita `winutils.exe` y `hadoop.dll`. Descárguelos de
  `https://github.com/cdarlint/winutils` (carpeta `hadoop-3.3.6/bin`), póngalos en
  `C:\hadoop\bin` y defina la variable de entorno `HADOOP_HOME=C:\hadoop`.
  En Linux/macOS no hace falta.

### 3. Dependencias de Big Data
Con el entorno virtual de la app activado:
```bash
pip install -r bigdata/requirements.txt
```
Esto agrega PySpark y numpy sin tocar el `requirements.txt` de la app (no afecta el despliegue en
Render).

### 4. Ejecutar el pipeline
```bash
python bigdata/pipeline.py
```
Tarda 3–5 minutos. Ejecuta: exportación de la BD de la app → generación de datos simulados → ingesta →
ETL → Spark SQL → modelo predictivo → streaming. Lee la base que indique el `.env` (SQLite o
MySQL en Docker): los pedidos reales de ese equipo entran como una fuente más.

### 5. Ver los resultados
Con la app corriendo, inicie sesión como administrador o gestor y abra **Big Data** en el menú
(`/admin/bigdata`). Los archivos para Power BI quedan en `bigdata/resultados/powerbi/`.

> Aunque no se ejecute el paso 4, la pantalla ya muestra los resultados que vienen en el repositorio
> (`bigdata/resultados/`). Ejecutarlo sirve para demostrar que funciona en vivo.

## Para la sustentación
- Ejecutar `python bigdata/pipeline.py` en vivo y mostrar la pantalla al terminar.
- La guía de qué decir en cada paso está en `bigdata/SUSTENTACION.md`.
- Si hay poco tiempo: `python bigdata/pipeline.py --sin-generacion` reutiliza datos ya generados,
  pero requiere haber corrido el pipeline completo antes en ese equipo.

## Problemas frecuentes

| Síntoma | Causa y solución |
|---|---|
| `HADOOP_HOME and hadoop.home.dir are unset` (Windows) | Falta el paso 2 de `winutils`; defina `HADOOP_HOME` y abra una terminal nueva |
| `JAVA_HOME is not set` / `java not found` | Instale Java 17+ y reinicie la terminal |
| `PYTHON_VERSION_MISMATCH` | Spark usa otro Python; ejecute siempre con el Python del entorno virtual |
| `ModuleNotFoundError: pyspark` o `numpy` | Falta el paso 3 en ese entorno virtual |
| Pantalla Big Data dice "Aún no hay resultados" | Falta el paso 4 o el repositorio no trajo `bigdata/resultados/` |
| `OutOfMemoryError` | `set SPARK_MEMORIA=2g` (Windows) o `export SPARK_MEMORIA=2g`, o use `--pedidos 100000` |
| Error de conexión a MySQL al exportar | El contenedor Docker debe estar arriba: `docker compose up -d` |
