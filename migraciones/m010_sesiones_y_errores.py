"""Sesion unica, cierre por inactividad y registro de errores (RNF5).

- `usuarios` gana `token_sesion` y `sesion_actividad_en` (ver
  app/services/sesiones.py). Quedan en NULL: las sesiones ya abiertas siguen
  validas hasta el siguiente inicio de sesion de cada cuenta.
- `configuracion_negocio` gana `minutos_inactividad`, con el valor inicial
  como DEFAULT, que SQLite exige en un `ALTER TABLE ADD COLUMN` NOT NULL.
- Se crea la tabla `errores_sistema` (ver app/models/error.py).

Como m008, `create_all` no agrega columnas a una tabla existente, de ahi esta
migracion. Es idempotente: solo agrega lo que falta.

    flask --app run migrar-seguridad
"""

from sqlalchemy import inspect, text

from app.extensions import db
from app.models import MINUTOS_INACTIVIDAD_INICIAL, ConfiguracionNegocio, ErrorSistema, Usuario

COLUMNAS = {
    Usuario: {
        "token_sesion": "",
        "sesion_actividad_en": "",
    },
    ConfiguracionNegocio: {
        "minutos_inactividad": f" NOT NULL DEFAULT {MINUTOS_INACTIVIDAD_INICIAL}",
    },
}


def aplicar(verboso=True):
    """Agrega columnas y tabla que falten. Devuelve la lista de lo agregado."""
    inspector = inspect(db.engine)
    tablas = inspector.get_table_names()
    agregado = []

    with db.engine.begin() as conexion:
        for modelo, columnas in COLUMNAS.items():
            tabla = modelo.__tablename__
            if tabla not in tablas:
                if verboso:
                    print(f"Tabla {tabla}: no existe; ejecute primero init-db.")
                continue
            existentes = {c["name"] for c in inspector.get_columns(tabla)}
            for nombre, restriccion in columnas.items():
                if nombre in existentes:
                    continue
                tipo = modelo.__table__.c[nombre].type.compile(dialect=db.engine.dialect)
                conexion.execute(
                    text(f"ALTER TABLE {tabla} ADD COLUMN {nombre} {tipo}{restriccion}")
                )
                agregado.append(f"{tabla}.{nombre}")

    if ErrorSistema.__tablename__ not in tablas:
        db.metadata.create_all(bind=db.engine, tables=[ErrorSistema.__table__], checkfirst=True)
        agregado.append(ErrorSistema.__tablename__)

    if verboso:
        print("Sesiones y errores: " + (f"agregado {', '.join(agregado)}." if agregado
                                        else "sin cambios."))
    return agregado
