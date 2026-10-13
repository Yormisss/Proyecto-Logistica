"""Reinicio de la base de datos antes de cada suite.

Funciona con cualquier motor configurado en DATABASE_URL: borrar el archivo de
SQLite no sirve cuando el proyecto apunta a MySQL, asi que se usa el comando
`reset-db`, que elimina y recrea el esquema a traves del ORM.
"""

import os
import pathlib
import subprocess
import sys

RAIZ = pathlib.Path(__file__).resolve().parent.parent
# El interprete que ya esta ejecutando la suite es el mismo que tiene las
# dependencias del proyecto instaladas, sea Windows, macOS o Linux. Una ruta
# fija como `.venv/bin/python` no existe en Windows (alli el entorno virtual
# usa `.venv/Scripts/python.exe`) y tampoco sirve si el entorno virtual vive
# en otro lugar.
PYTHON = sys.executable

# Las pruebas nunca avisan al escenario real de Make: sin estas variables, el
# .env del desarrollador aportaria su webhook (load_dotenv no pisa una variable
# ya definida, asi que vacias ganan). Las suites que prueban los avisos ponen
# su propia URL simulada en app.config.
ENTORNO_PRUEBAS = {**os.environ, "MAKE_WEBHOOK_URL": "", "MAKE_WEBHOOK_CREDENCIALES_URL": ""}


def reiniciar_base():
    """Deja la base vacia y vuelve a sembrarla con los datos de demostracion."""
    subprocess.run(
        [str(PYTHON), "-m", "flask", "--app", "run", "reset-db"],
        cwd=RAIZ, capture_output=True, env=ENTORNO_PRUEBAS,
    )
    subprocess.run([str(PYTHON), "seed.py"], cwd=RAIZ, capture_output=True, env=ENTORNO_PRUEBAS)
