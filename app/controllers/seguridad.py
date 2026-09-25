"""Decoradores de autorizacion por rol (RF1 / RNF5).

Evitan que personal no autorizado acceda a las rutas de entrega o altere el inventario.
"""

from functools import wraps
from urllib.parse import urlsplit

from flask import abort, url_for
from flask_login import current_user

from app.models import Rol

# Pagina de inicio de cada rol. Es la unica fuente de verdad del aterrizaje tras
# el login: antes la decision era un `if es_conductor / else dashboard`, que
# enviaba cualquier rol nuevo al tablero administrativo para recibir un 403.
INICIO_POR_ROL = {
    Rol.ADMIN: "admin.dashboard",
    Rol.DESPACHADOR: "admin.dashboard",
    Rol.CONDUCTOR: "conductor.mi_ruta",
    Rol.CLIENTE: "cliente.mis_pedidos",
}

# Destino de reserva para un rol sin pagina propia: el perfil es accesible a
# cualquier sesion autenticada, asi que nunca produce un bucle de redirecciones.
INICIO_POR_DEFECTO = "auth.perfil"


def destino_por_rol(usuario):
    """URL de aterrizaje del usuario segun su rol: tablero, ruta del dia o portal."""
    return url_for(INICIO_POR_ROL.get(usuario.rol, INICIO_POR_DEFECTO))


def es_redireccion_segura(destino):
    """True si `destino` es una ruta relativa al propio sitio (RNF5).

    El parametro `next` del login lo escribe el cliente, asi que no basta con
    exigir que empiece por "/": un navegador resuelve "//evil.com" y
    "/\\evil.com" como una URL hacia otro host (la barra invertida se
    normaliza a "/" antes de interpretar la URL en varios navegadores), y ahi
    el "/" inicial no protege nada. Se rechaza cualquier variante con
    esquema o dominio explicito o implicito.
    """
    if not destino:
        return False
    if "\\" in destino:
        return False
    if not destino.startswith("/") or destino.startswith("//"):
        return False

    partes = urlsplit(destino)
    return not partes.scheme and not partes.netloc


def requiere_rol(*roles):
    def decorador(vista):
        @wraps(vista)
        def envoltura(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if current_user.rol not in roles:
                abort(403)
            return vista(*args, **kwargs)

        return envoltura

    return decorador
