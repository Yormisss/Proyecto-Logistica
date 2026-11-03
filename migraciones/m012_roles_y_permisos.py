"""Roles con permisos configurables (RF1 / RNF5, ver app/permisos.py).

Crea `roles`, `rol_permisos`, `usuario_roles` y `usuario_permisos`, y los
cuatro roles del sistema con el reparto que tenian cuando eran fijos: nadie
gana ni pierde acceso al aplicarla. Como m004, solo agrega tablas nuevas;
`create_all` con `checkfirst=True` las crea en SQLite y en MySQL.

Es idempotente: no repite tablas ni roles, y no toca un reparto ya editado.

    flask --app run migrar-seguridad
"""

from sqlalchemy import inspect

from app.extensions import db
from app.models import PermisoUsuario, RolAcceso, RolPermiso, usuario_roles
from app.services.permisos import asegurar_roles

TABLAS = (RolAcceso.__table__, RolPermiso.__table__, usuario_roles, PermisoUsuario.__table__)


def aplicar(verboso=True):
    """Crea las tablas y los roles del sistema que falten. Devuelve lo agregado."""
    existentes = set(inspect(db.engine).get_table_names())
    if "usuarios" not in existentes:
        if verboso:
            print("Tabla usuarios: no existe; ejecute primero init-db.")
        return []
    db.metadata.create_all(bind=db.engine, tables=list(TABLAS), checkfirst=True)
    agregado = [t.name for t in TABLAS if t.name not in existentes]
    creados = asegurar_roles()
    db.session.commit()
    if creados:
        agregado.append("roles del sistema: " + ", ".join(creados))
    if verboso:
        print("Roles y permisos: " + (f"{'; '.join(agregado)}." if agregado else "sin cambios."))
    return agregado
