"""Cifra el documento y el telefono de las cuentas (RNF5, ver app/cifrado.py).

En `usuarios`:

1. Amplia `documento` y `telefono` a VARCHAR(300): el texto cifrado ocupa
   hasta 250 caracteres. SQLite no limita el largo de VARCHAR y se omite.
2. Quita el indice unico de `documento` (MySQL): sobre texto cifrado con IV
   aleatorio no impide nada. La unicidad pasa a `documento_indice`. En SQLite
   ese indice es parte de la tabla y no se puede quitar sin reconstruirla;
   queda, y no estorba.
3. Agrega `documento_indice` y cifra los valores que sigan en claro,
   calculando su indice ciego. Lee y escribe con SQL directo: el ORM ya
   descifraria al leer y fallaria con un valor en claro.
4. Crea el indice unico de `documento_indice` cuando ya esta lleno.

Es idempotente: un valor que ya descifra con la clave actual no se toca.
Use la misma CLAVE_CIFRADO (o SECRET_KEY) con la que va a correr la aplicacion.

    flask --app run migrar-seguridad
"""

from sqlalchemy import inspect, text

from app.cifrado import cifrar, descifrar, esta_cifrado, indice_ciego
from app.extensions import db
from app.models import Usuario

TABLA = Usuario.__tablename__
INDICE = "ix_usuarios_documento_indice"
LARGO = 300


def _ampliar(conexion, inspector, agregado):
    if db.engine.dialect.name != "mysql":
        return
    for columna in inspector.get_columns(TABLA):
        if columna["name"] in ("documento", "telefono") and (columna["type"].length or 0) < LARGO:
            conexion.execute(text(f"ALTER TABLE {TABLA} MODIFY {columna['name']} VARCHAR({LARGO}) NULL"))
            agregado.append(f"{TABLA}.{columna['name']} VARCHAR({LARGO})")


def _quitar_unico_documento(conexion, inspector, agregado):
    if db.engine.dialect.name != "mysql":
        return
    for indice in inspector.get_indexes(TABLA):
        if indice.get("unique") and indice["column_names"] == ["documento"]:
            conexion.execute(text(f"ALTER TABLE {TABLA} DROP INDEX `{indice['name']}`"))
            agregado.append(f"sin indice unico en {TABLA}.documento")


def _cifrar_filas(conexion):
    cifradas = 0
    filas = conexion.execute(
        text(f"SELECT id, documento, telefono, documento_indice FROM {TABLA}")
    ).all()
    for id_, documento, telefono, indice in filas:
        cambios = {}
        if documento is not None:
            en_claro = descifrar(documento) if esta_cifrado(documento) else documento
            if en_claro == documento:
                cambios["documento"] = cifrar(documento)
            if indice != indice_ciego(en_claro):
                cambios["documento_indice"] = indice_ciego(en_claro)
        if telefono is not None and not esta_cifrado(telefono):
            cambios["telefono"] = cifrar(telefono)
        if cambios:
            asignaciones = ", ".join(f"{campo} = :{campo}" for campo in cambios)
            conexion.execute(text(f"UPDATE {TABLA} SET {asignaciones} WHERE id = :id"),
                             {**cambios, "id": id_})
            cifradas += 1
    return cifradas


def aplicar(verboso=True):
    """Amplia, cifra e indexa lo que falte. Devuelve la lista de lo hecho."""
    inspector = inspect(db.engine)
    if TABLA not in inspector.get_table_names():
        if verboso:
            print(f"Tabla {TABLA}: no existe; ejecute primero init-db.")
        return []

    agregado = []
    with db.engine.begin() as conexion:
        _ampliar(conexion, inspector, agregado)
        _quitar_unico_documento(conexion, inspector, agregado)
        if "documento_indice" not in {c["name"] for c in inspector.get_columns(TABLA)}:
            conexion.execute(text(f"ALTER TABLE {TABLA} ADD COLUMN documento_indice VARCHAR(64)"))
            agregado.append(f"{TABLA}.documento_indice")
        cifradas = _cifrar_filas(conexion)
        if cifradas:
            agregado.append(f"{cifradas} cuenta(s) cifradas")
        if INDICE not in {i["name"] for i in inspector.get_indexes(TABLA)}:
            conexion.execute(text(f"CREATE UNIQUE INDEX {INDICE} ON {TABLA} (documento_indice)"))
            agregado.append(INDICE)

    if verboso:
        print("Cifrado de datos personales: " + (f"{'; '.join(agregado)}." if agregado
                                                 else "sin cambios."))
    return agregado
