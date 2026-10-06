"""Introduce la tabla `solicitudes_contacto` (asistente de voz del cliente).

Como m002, solo agrega una tabla nueva con sus claves ajenas hacia `clientes`
y `usuarios`; `create_all` con `checkfirst=True` la crea completa en SQLite y
en MySQL, y no hace nada si ya existe.

    flask --app run migrar-asistente
"""

from sqlalchemy import inspect

from app.extensions import db
from app.models import SolicitudContacto

TABLA = SolicitudContacto.__tablename__


def aplicar(verboso=True):
    """Crea la tabla si falta. Devuelve True si la creo en esta ejecucion."""
    existia = TABLA in inspect(db.engine).get_table_names()
    db.metadata.create_all(bind=db.engine, tables=[SolicitudContacto.__table__], checkfirst=True)
    if verboso:
        print(f"Tabla {TABLA}: " + ("ya existia, sin cambios." if existia else "creada."))
    return not existia
