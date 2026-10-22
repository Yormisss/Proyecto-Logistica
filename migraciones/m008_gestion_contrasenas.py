"""Agrega a `usuarios` las columnas de la gestion de contrasenas.

`contrasena_actualizada_en`, `debe_cambiar_contrasena` y `version_credencial`
sostienen el apartado *Contrasenas* del administrador. Una base creada con
`init-db` despues de este cambio ya las tiene; `create_all` no agrega columnas
a una tabla existente, de ahi esta migracion.

Las columnas NOT NULL se agregan con DEFAULT 0, que SQLite exige en un
`ALTER TABLE ADD COLUMN` y que deja a las cuentas existentes sin exigencia de
cambio y con su sesion vigente. La fecha queda en NULL: no se sabe cuando se
cambio por ultima vez cada contrasena, y el apartado lo muestra asi.

Es idempotente: solo agrega las columnas que faltan.

    flask --app run migrar-contrasenas
"""

from sqlalchemy import inspect, text

from app.extensions import db
from app.models import Usuario

TABLA = Usuario.__tablename__
COLUMNAS = {
    "contrasena_actualizada_en": "",
    "debe_cambiar_contrasena": " NOT NULL DEFAULT 0",
    "version_credencial": " NOT NULL DEFAULT 0",
}


def aplicar(verboso=True):
    """Agrega las columnas que falten. Devuelve la lista de columnas agregadas."""
    inspector = inspect(db.engine)
    if TABLA not in inspector.get_table_names():
        if verboso:
            print(f"Tabla {TABLA}: no existe; ejecute primero init-db.")
        return []

    existentes = {c["name"] for c in inspector.get_columns(TABLA)}
    agregadas = []
    with db.engine.begin() as conexion:
        for nombre, restriccion in COLUMNAS.items():
            if nombre in existentes:
                continue
            tipo = Usuario.__table__.c[nombre].type.compile(dialect=db.engine.dialect)
            conexion.execute(
                text(f"ALTER TABLE {TABLA} ADD COLUMN {nombre} {tipo}{restriccion}")
            )
            agregadas.append(nombre)

    if verboso:
        print(
            f"Tabla {TABLA}: "
            + (f"columnas agregadas: {', '.join(agregadas)}." if agregadas else "sin cambios.")
        )
    return agregadas
