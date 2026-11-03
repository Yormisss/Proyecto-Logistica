"""Introduce la tabla `intentos_acceso` (bloqueo del login por intentos fallidos).

Como m004, solo agrega una tabla nueva, en este caso sin claves ajenas (ver
`app/models/acceso.py`); `create_all` con `checkfirst=True` la crea completa en
SQLite y en MySQL, y no hace nada si ya existe.

    flask --app run migrar-contrasenas
"""

from sqlalchemy import inspect

from app.extensions import db
from app.models import IntentoAcceso

TABLA = IntentoAcceso.__tablename__


def aplicar(verboso=True):
    """Crea la tabla si falta. Devuelve True si la creo en esta ejecucion."""
    existia = TABLA in inspect(db.engine).get_table_names()
    db.metadata.create_all(bind=db.engine, tables=[IntentoAcceso.__table__], checkfirst=True)
    if verboso:
        print(f"Tabla {TABLA}: " + ("ya existia, sin cambios." if existia else "creada."))
    return not existia
