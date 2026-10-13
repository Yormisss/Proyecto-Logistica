"""Pruebas de entrega de solo agregar: una por cada cierre del pedido.

Antes cada pedido tenia una sola prueba de entrega (indice unico en
`pruebas_entrega.pedido_id`) y un reintento sobrescribia la del intento
fallido. Esta migracion quita ese indice unico y conserva todas las filas:

1. Crea el indice normal `ix_pruebas_entrega_pedido_id` si falta. Va primero
   porque MySQL exige un indice sobre la columna de la clave ajena.
2. Quita cada indice unico que cubra solo `pedido_id`.

Es idempotente: en una base al dia no hace nada. Solo MySQL; en SQLite el
indice unico va dentro de la tabla y no se puede quitar sin reconstruirla
(use `reset-db`).

    flask --app run migrar-pruebas-entrega
"""

from sqlalchemy import inspect, text

from app.extensions import db

TABLA = "pruebas_entrega"
INDICE = "ix_pruebas_entrega_pedido_id"


def _unicos(inspector):
    nombres = {i["name"] for i in inspector.get_indexes(TABLA)
               if i.get("unique") and i["column_names"] == ["pedido_id"]}
    nombres |= {u["name"] for u in inspector.get_unique_constraints(TABLA)
                if u["column_names"] == ["pedido_id"]}
    return sorted(n for n in nombres if n)


def aplicar(verboso=True):
    """Aplica lo que falte. Devuelve un diccionario con lo que hizo."""
    hecho = {"indices": [], "unicos_quitados": []}
    inspector = inspect(db.engine)
    unicos = _unicos(inspector)
    if db.engine.dialect.name != "mysql":
        if unicos:
            raise RuntimeError("m007 solo corre en MySQL. En SQLite reconstruya la base con reset-db.")
        return hecho

    with db.engine.begin() as conexion:
        if INDICE not in {i["name"] for i in inspector.get_indexes(TABLA)}:
            conexion.execute(text(f"CREATE INDEX {INDICE} ON {TABLA} (pedido_id)"))
            hecho["indices"].append(INDICE)
        for nombre in unicos:
            conexion.execute(text(f"ALTER TABLE {TABLA} DROP INDEX `{nombre}`"))
            hecho["unicos_quitados"].append(nombre)

    if verboso:
        if any(hecho.values()):
            print(f"Pruebas de entrega: indice {', '.join(hecho['indices']) or 'ya existia'}; "
                  f"unico quitado: {', '.join(hecho['unicos_quitados']) or 'ninguno'}.")
        else:
            print("Pruebas de entrega: sin cambios.")
    return hecho
