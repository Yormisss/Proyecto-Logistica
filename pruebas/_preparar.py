"""Reinicio de la base de datos antes de cada suite.

Funciona con cualquier motor configurado en DATABASE_URL: borrar el archivo de
SQLite no sirve cuando el proyecto apunta a MySQL, asi que se usa el comando
`reset-db`, que elimina y recrea el esquema a traves del ORM.
"""

import pathlib
import subprocess

RAIZ = pathlib.Path(__file__).resolve().parent.parent
PYTHON = RAIZ / ".venv" / "bin" / "python"


def reiniciar_base():
    """Deja la base vacia y vuelve a sembrarla con los datos de demostracion."""
    subprocess.run(
        [str(PYTHON), "-m", "flask", "--app", "run", "reset-db"],
        cwd=RAIZ, capture_output=True,
    )
    subprocess.run([str(PYTHON), "seed.py"], cwd=RAIZ, capture_output=True)
