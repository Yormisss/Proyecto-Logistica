"""Agrega a `sesiones_asistente` las columnas de la confirmacion de acciones.

`confirmacion_firma` y `confirmacion_vence_en` guardan la accion que el
asistente resumio y espera confirmar. Una base creada con `init-db` despues de
este cambio ya las tiene; esta migracion es para una base que ya tenia la
tabla de m002, a la que `create_all` no le agrega columnas.

Es idempotente: solo agrega las columnas que faltan. Si la tabla no existe no
hace nada (m002 la crea completa).

    flask --app run migrar-asistente
"""

from sqlalchemy import inspect, text

from app.extensions import db
from app.models import SesionAsistente

TABLA = SesionAsistente.__tablename__
COLUMNAS = ("confirmacion_firma", "confirmacion_vence_en")


def aplicar(verboso=True):
    """Agrega las columnas que falten. Devuelve la lista de columnas agregadas."""
    inspector = inspect(db.engine)
    if TABLA not in inspector.get_table_names():
        if verboso:
            print(f"Tabla {TABLA}: no existe; ejecute primero m002.")
        return []

    existentes = {c["name"] for c in inspector.get_columns(TABLA)}
    agregadas = []
    with db.engine.begin() as conexion:
        for nombre in COLUMNAS:
            if nombre in existentes:
                continue
            tipo = SesionAsistente.__table__.c[nombre].type.compile(dialect=db.engine.dialect)
            conexion.execute(text(f"ALTER TABLE {TABLA} ADD COLUMN {nombre} {tipo}"))
            agregadas.append(nombre)

    if verboso:
        print(
            f"Tabla {TABLA}: "
            + (f"columnas agregadas: {', '.join(agregadas)}." if agregadas else "sin cambios.")
        )
    return agregadas
