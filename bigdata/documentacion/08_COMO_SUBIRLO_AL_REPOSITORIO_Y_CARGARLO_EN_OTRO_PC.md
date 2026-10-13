# CÓMO SUBIRLO AL REPOSITORIO Y CÓMO LO CARGAN LOS COMPAÑEROS EN LA APP CON MAKE Y RETELL

## Situación

Los compañeros ya tienen la app corriendo con el agente de IA (Retell), las automatizaciones (Make) y MySQL
en Docker. La idea es que el módulo de Big Data se cargue en **su** app y se sustente en **su** PC, sin que
quien lo desarrolló tenga que implementar nada del agente.

**El módulo no toca el agente de IA.** No modifica el asistente de voz, las automatizaciones ni los modelos
de la base de datos; solo **lee** la base para exportar los pedidos y agrega una pantalla nueva. No necesita
claves de Retell ni de Make.

## Qué cambia en el repositorio

| Archivo | Cambio |
|---|---|
| `bigdata/` | Carpeta nueva con el pipeline, documentación, muestras y resultados |
| `app/controllers/bigdata.py` | Controlador nuevo |
| `app/views/admin/bigdata.html` | Vista nueva |
| `app/__init__.py` | +2 líneas: registrar el controlador |
| `app/views/base.html` | +2 líneas: enlace "Big Data" en el menú |
| `.gitignore` | Ignora `bigdata/datalake/` (cientos de MB, se regenera) |

---

## PARTE A: Quien lo desarrolló lo sube al repositorio

El repositorio es `https://github.com/juansantoya/proyecto-logistica`. Hay dos casos:

### Caso 1: es colaborador con permiso de escritura (lo más limpio)

```bash
cd proyecto-logistica
git checkout -b bigdata
git add bigdata app/controllers/bigdata.py app/views/admin/bigdata.html app/__init__.py app/views/base.html .gitignore
git status                      # revisar que NO aparezca bigdata/datalake ni .env
git commit -m "Agrega módulo de Big Data con Apache Spark"
git push -u origin bigdata
```

Los compañeros trabajan con la rama `bigdata` sin tocar `main`. Cuando se valide, se hace el Pull Request o el merge.

### Caso 2: no tiene permiso de escritura

- **Opción A:** hacer un *fork* del repositorio en GitHub, subir ahí la rama y abrir un **Pull Request** al original.
- **Opción B:** enviar la carpeta `bigdata/` y los 4 archivos modificados a un compañero con permiso para que los suba.

### Antes de subir, verificar

- Que `git status` **no** muestre `bigdata/datalake/` (pesa más de 300 MB).
- Que **no** aparezca ningún `.env` (lleva claves).
- Que sí aparezcan `bigdata/muestras/` y `bigdata/resultados/`.

---

## PARTE B: Los compañeros lo cargan en su app

### 1. Traer los cambios
```bash
git fetch origin
git checkout bigdata            # o: git pull origin main, si ya se hizo merge
```
Si hay cambios locales sin guardar, hágase `git stash` antes y `git stash pop` después.

### 2. Requisitos (una sola vez)
- **Java 17 o superior** (`java -version`).
- **Python 3.10+**, el mismo del entorno virtual de la app.
- **Solo en Windows:** `winutils.exe` y `hadoop.dll` (ver `04_QUE_HAY_QUE_INSTALAR_Y_POR_QUE.md`).

### 3. Dependencias de Big Data
Con el entorno virtual de la app activado:
```bash
pip install -r bigdata/requirements.txt
```

### 4. Ejecutar el pipeline
```bash
python bigdata/pipeline.py
```
Tarda 3–5 minutos. Lee la base que indique el `.env` (SQLite o el MySQL de Docker); sus pedidos reales
entran como una fuente más. **Con el contenedor de MySQL arriba** (`docker compose up -d`).

### 5. Ver los resultados
Con la app corriendo, iniciar sesión como administrador o gestor y abrir **Big Data** en el menú
(`/admin/bigdata`).

> Aunque no se ejecute el paso 4, la pantalla ya muestra los resultados que vienen en el repositorio
> (`bigdata/resultados/`). Ejecutarlo sirve para demostrar que funciona en vivo.

---

## PARTE C: Para la sustentación en el PC de ellos

1. Tener la app corriendo (con Make y Retell como siempre) y el pipeline ya ejecutado **una vez** antes de la
   sustentación, para confirmar que corre en ese equipo.
2. En la sustentación: ejecutar `python bigdata/pipeline.py` en vivo y mostrar la pantalla al terminar.
3. Si hay poco tiempo: `python bigdata/pipeline.py --sin-generacion` reutiliza los datos ya generados (requiere
   haber corrido el pipeline completo antes en ese equipo).
4. Como respaldo, la pantalla ya muestra los resultados guardados aunque el pipeline no corra.

## Lo que no se pudo verificar

El módulo se probó en un PC con Windows, Java 24 y SQLite. **No se ha probado con el MySQL de Docker** de los
compañeros ni en sus equipos. Por eso conviene que uno de ellos lo ejecute **antes** de la sustentación y
confirme que el pipeline corre.
