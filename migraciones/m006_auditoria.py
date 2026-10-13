"""Auditoria de acciones (voz, configuracion y usuarios).

Crea la tabla `auditoria` con sus indices si falta. Es idempotente: en una base
que ya la tiene no hace nada.

    flask --app run migrar-auditoria
"""

from sqlalchemy import inspect

from app.extensions import db
from app.models import Auditoria


def aplicar(verboso=True):
    """Aplica lo que falte. Devuelve un diccionario con lo que hizo."""
    existentes = set(inspect(db.engine).get_table_names())
    hecho = {"tablas": [] if Auditoria.__tablename__ in existentes else [Auditoria.__tablename__]}
    db.metadata.create_all(bind=db.engine, tables=[Auditoria.__table__], checkfirst=True)
    if verboso:
        print("Auditoria: " + ("tabla creada." if hecho["tablas"] else "sin cambios."))
    return hecho
