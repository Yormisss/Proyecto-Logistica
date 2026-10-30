"""Registro de errores del sistema (error logs).

Toda excepcion no controlada de una peticion queda en la tabla
`errores_sistema`, que el administrador consulta en Administracion > Errores,
y en el archivo instance/logs/errores.log. El archivo es el respaldo: si el
error es justamente que la base no responde, la fila no se puede escribir.

Se engancha a la senal `got_request_exception`, que Flask emite tambien en
modo desarrollo, cuando el depurador muestra la traza en lugar de la pagina
500. El numero del registro queda en `g.error_id`, y la pagina 500 lo muestra
como referencia para buscarlo en la pantalla de errores.
"""

import logging
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import g, got_request_exception, request

from app.extensions import db
from app.models import ErrorSistema
from app.tiempo import ahora

registro = logging.getLogger("rutalytics.errores")

ARCHIVO = "errores.log"
LARGO_MENSAJE = 1000


def configurar(app):
    """Abre el archivo de errores y conecta la senal. Una vez por aplicacion."""
    carpeta = Path(app.instance_path) / "logs"
    carpeta.mkdir(parents=True, exist_ok=True)
    # El logger es global: las pruebas crean varias aplicaciones en un proceso.
    if not registro.handlers:
        manejador = RotatingFileHandler(carpeta / ARCHIVO, maxBytes=1_000_000, backupCount=5,
                                        encoding="utf-8")
        manejador.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        registro.addHandler(manejador)
        registro.setLevel(logging.ERROR)
    got_request_exception.connect(_al_fallar, app)


def _al_fallar(app, exception, **_):
    # Un fallo al registrar no debe tapar el error original ni su pagina 500.
    try:
        g.error_id = registrar(exception)
    except Exception:
        app.logger.exception("No se pudo registrar el error de la peticion.")


def _usuario():
    """La cuenta de la peticion, o None. Nunca falla: el error pudo ser al cargarla."""
    try:
        from flask_login import current_user

        return current_user._get_current_object() if current_user.is_authenticated else None
    except Exception:
        return None


def _tipo(excepcion):
    clase = type(excepcion)
    if clase.__module__ == "builtins":
        return clase.__qualname__
    return f"{clase.__module__}.{clase.__qualname__}"


def registrar(excepcion):
    """Guarda el error de la peticion actual. Devuelve su numero, o None si la
    base no lo pudo guardar (queda igual en el archivo)."""
    # La transaccion de la peticion fallo: se descarta antes de escribir, para
    # no confirmar a medias lo que la vista dejo pendiente.
    try:
        db.session.rollback()
    except Exception:
        pass

    usuario = _usuario()
    datos = {
        "registrado_en": ahora(),
        "metodo": request.method,
        "ruta": request.path[:500],
        "endpoint": (request.endpoint or "")[:80] or None,
        "usuario_id": usuario.id if usuario else None,
        "usuario_nombre": usuario.nombre if usuario else None,
        "ip": (request.remote_addr or "")[:45] or None,
        "tipo": _tipo(excepcion)[:200],
        "mensaje": (str(excepcion) or "(sin mensaje)")[:LARGO_MENSAJE],
        # Con tres argumentos: el de uno solo no existe en Python 3.9.
        "traza": "".join(traceback.format_exception(type(excepcion), excepcion,
                                                    excepcion.__traceback__)),
    }

    numero = None
    try:
        with db.engine.begin() as conexion:
            resultado = conexion.execute(ErrorSistema.__table__.insert().values(**datos))
            numero = resultado.inserted_primary_key[0]
    except Exception:
        pass

    registro.error(
        "Error #%s en %s %s (usuario: %s): %s: %s\n%s",
        numero or "sin registrar en la base", datos["metodo"], datos["ruta"],
        datos["usuario_nombre"] or "anonimo", datos["tipo"], datos["mensaje"], datos["traza"],
    )
    return numero
