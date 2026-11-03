"""Las llamadas de voz siguen a la sesion web que las abrio (RNF5).

`sesiones_asistente` gana `huella_sesion` (ver app/models/asistente.py): el
SHA-256 del identificador de la sesion web que abrio la llamada. Si la cuenta
entra en otro navegador, cierra sesion o cambia de contrasena, la llamada deja
de autorizar funciones. Queda en NULL en las sesiones ya abiertas, que se
validan como antes hasta que vencen (15 minutos).

Como m010, `create_all` no agrega columnas a una tabla existente, de ahi esta
migracion. Es idempotente: solo agrega lo que falta.

    flask --app run migrar-seguridad
"""

from sqlalchemy import inspect, text

from app.extensions import db
from app.models import SesionAsistente

TABLA = SesionAsistente.__tablename__
COLUMNA = "huella_sesion"


def aplicar(verboso=True):
    """Agrega la columna si falta. Devuelve la lista de lo agregado."""
    inspector = inspect(db.engine)
    if TABLA not in inspector.get_table_names():
        if verboso:
            print(f"Tabla {TABLA}: no existe; ejecute primero init-db.")
        return []

    agregado = []
    if COLUMNA not in {c["name"] for c in inspector.get_columns(TABLA)}:
        tipo = SesionAsistente.__table__.c[COLUMNA].type.compile(dialect=db.engine.dialect)
        with db.engine.begin() as conexion:
            conexion.execute(text(f"ALTER TABLE {TABLA} ADD COLUMN {COLUMNA} {tipo}"))
        agregado.append(f"{TABLA}.{COLUMNA}")

    if verboso:
        print("Llamadas de voz y sesion web: " + (f"agregado {', '.join(agregado)}." if agregado
                                                   else "sin cambios."))
    return agregado
