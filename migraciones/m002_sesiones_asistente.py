"""Introduce la tabla `sesiones_asistente` del asistente de voz.

A diferencia de m001, este cambio no toca tablas existentes: solo agrega una
tabla nueva con su clave ajena hacia `usuarios`. `create_all` con
`checkfirst=True` la crea completa (columnas, indices y clave ajena) en SQLite
y en MySQL, y no hace nada si ya existe.

Es idempotente: volver a ejecutarla no cambia nada.

    flask --app run migrar-asistente
"""

from sqlalchemy import inspect

from app.extensions import db
from app.models import SesionAsistente

TABLA = SesionAsistente.__tablename__


def aplicar(verboso=True):
    """Crea la tabla si falta. Devuelve True si la creo en esta ejecucion."""
    existia = TABLA in inspect(db.engine).get_table_names()

    db.metadata.create_all(
        bind=db.engine,
        tables=[SesionAsistente.__table__],
        checkfirst=True,
    )

    if verboso:
        print(
            f"Tabla {TABLA}: "
            + ("ya existia, sin cambios." if existia else "creada.")
        )

    return not existia
