"""RNF5 - Sesion unica por cuenta y cierre por inactividad.

Sesion unica: cada inicio de sesion genera un `token_sesion` nuevo para la
cuenta, que forma parte del identificador de sesion (`Usuario.get_id`). La
sesion abierta antes en otro navegador conserva el token anterior y
`cargar_usuario` la rechaza: entrar en un navegador cierra la del otro, y la
cookie de "Mantener sesion iniciada" de ese otro navegador deja de servir.
Cerrar sesion borra el token, asi que tampoco sirve una copia de la cookie.

Inactividad: la cuenta guarda la hora de su ultima peticion
(`sesion_actividad_en`). Si pasan mas minutos de los que fija el
administrador en Configuracion, la siguiente peticion cierra la sesion. Se
guarda en la base y no en la cookie para que "Mantener sesion iniciada"
tampoco la salte al reabrir el navegador.

La aplicacion de ambas reglas en cada peticion vive en
`app.controllers.auth.vigilar_sesion`. Ninguna funcion hace commit, salvo
`registrar_actividad`, que escribe en su propia transaccion.
"""

import secrets

from app.extensions import db
from app.models import Usuario
from app.models.usuario import partes_identificador
from app.services.configuracion import minutos_inactividad
from app.tiempo import ahora

MENSAJE_OTRA_SESION = ("Su sesion se cerro porque la cuenta inicio sesion en otro navegador "
                       "o dispositivo.")


def iniciar(usuario):
    """Token nuevo para la sesion que esta por abrirse; la anterior caduca."""
    usuario.token_sesion = secrets.token_urlsafe(32)
    usuario.sesion_actividad_en = ahora()


def cerrar(usuario):
    """Borra el token: ninguna cookie de esta cuenta vuelve a servir."""
    usuario.token_sesion = None


def segundos_restantes(usuario, limite=None):
    """Segundos que le quedan a la sesion antes de cerrarse por inactividad.

    `limite` en segundos; sin el, el de la configuracion.
    """
    if limite is None:
        limite = minutos_inactividad() * 60
    if usuario.sesion_actividad_en is None:
        # Sesion abierta antes de la migracion m010: cuenta desde ahora.
        return limite
    transcurridos = (ahora() - usuario.sesion_actividad_en).total_seconds()
    return max(0, int(limite - transcurridos))


def registrar_actividad(usuario):
    """Marca la peticion actual como actividad de la sesion.

    En su propia transaccion: las vistas de consulta no hacen commit, y la
    marca debe quedar aunque la peticion no escriba nada.
    """
    tabla = Usuario.__table__
    with db.engine.begin() as conexion:
        conexion.execute(
            tabla.update().where(tabla.c.id == usuario.id).values(sesion_actividad_en=ahora())
        )


def motivo_cierre(identificador):
    """Mensaje para una sesion que se cerro porque la cuenta entro en otro lado.

    `identificador` es el `_user_id` que quedo en la cookie de una sesion que
    `cargar_usuario` ya rechazo. Devuelve None si el rechazo fue por otra causa
    (cuenta inactiva, contrasena cambiada, sesion cerrada): esas ya tienen su
    propio aviso o no necesitan uno.
    """
    if not identificador:
        return None
    usuario_id, version, token = partes_identificador(identificador)
    usuario = db.session.get(Usuario, usuario_id) if usuario_id is not None else None
    if usuario is None or not usuario.activo or not usuario.token_sesion:
        return None
    if version == str(usuario.version_credencial or 0) and token != usuario.token_sesion:
        return MENSAJE_OTRA_SESION
    return None
