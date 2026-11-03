"""Correo de avisos de cada cuenta (`usuarios.correo_avisos`).

Los avisos de Make llegan a cada perfil segun su rol (ver
app/services/notificaciones.py): el conductor, su ruta; los gestores, el stock
bajo; los administradores, las acciones del asistente... `correo_avisos` es
adonde le llegan a esa cuenta. Es opcional: queda en NULL en las cuentas
existentes, que reciben los avisos en su correo de inicio de sesion.

Como m010, `create_all` no agrega columnas a una tabla existente, de ahi esta
migracion. Es idempotente: solo agrega lo que falta.

    flask --app run migrar-seguridad
"""

from sqlalchemy import inspect, text

from app.extensions import db
from app.models import Usuario

TABLA = Usuario.__tablename__
COLUMNA = "correo_avisos"


def aplicar(verboso=True):
    """Agrega la columna si falta. Devuelve la lista de lo agregado."""
    inspector = inspect(db.engine)
    if TABLA not in inspector.get_table_names():
        if verboso:
            print(f"Tabla {TABLA}: no existe; ejecute primero init-db.")
        return []

    agregado = []
    if COLUMNA not in {c["name"] for c in inspector.get_columns(TABLA)}:
        tipo = Usuario.__table__.c[COLUMNA].type.compile(dialect=db.engine.dialect)
        with db.engine.begin() as conexion:
            conexion.execute(text(f"ALTER TABLE {TABLA} ADD COLUMN {COLUMNA} {tipo}"))
        agregado.append(f"{TABLA}.{COLUMNA}")

    if verboso:
        print("Correo de avisos: " + (f"agregado {', '.join(agregado)}." if agregado else "sin cambios."))
    return agregado
