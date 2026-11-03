"""Permiso "Enviar el resumen del día" (resumen.enviar) en el rol del gestor.

En una base nueva el rol del sistema "Gestor logistico" ya lo trae (ver
PERMISOS_POR_DEFECTO en app/permisos.py). En una base con los roles de m012,
el reparto vive en `rol_permisos` y no cambia solo: esta migracion le agrega
el permiso, para que el gestor pueda enviar el resumen como antes lo recibia
por el escenario programado. El administrador lo tiene siempre.

Es idempotente: si el rol ya lo tiene, no hace nada. Si despues se lo quita en
Administracion > Roles, no vuelva a ejecutarla.

    flask --app run migrar-seguridad
"""

from sqlalchemy import inspect

from app.extensions import db
from app.models import Rol, RolAcceso, RolPermiso
from app.services.permisos import olvidar_permisos

PERMISO = "resumen.enviar"


def aplicar(verboso=True):
    """Agrega el permiso al rol del gestor si falta. Devuelve True si lo agrego."""
    if RolAcceso.__tablename__ not in inspect(db.engine).get_table_names():
        if verboso:
            print("Tabla roles: no existe; ejecute primero m012.")
        return False
    gestor = db.session.query(RolAcceso).filter_by(clave_sistema=Rol.DESPACHADOR).first()
    agregado = gestor is not None and PERMISO not in gestor.permisos
    if agregado:
        gestor.asignaciones.append(RolPermiso(permiso=PERMISO))
        db.session.commit()
        olvidar_permisos()
    if verboso:
        print("Permiso de enviar el resumen: " + ("agregado al gestor." if agregado else "sin cambios."))
    return agregado
