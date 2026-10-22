"""Decoradores de autorizacion por rol (RF1 / RNF5).

Evitan que personal no autorizado acceda a las rutas de entrega o altere el inventario.
"""

import re
from functools import wraps
from urllib.parse import urlsplit

from flask import abort, url_for
from flask_login import current_user
from wtforms import StringField
from wtforms.validators import DataRequired, Email, Length, Optional, StopValidation

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


# --------------------------------------------------------------------------
# Correo electronico: lista blanca de caracteres (RNF5)
# --------------------------------------------------------------------------

# La de `usuarios.correo`, `clientes.correo` y `solicitudes_contacto.correo`.
LONGITUD_MAXIMA_CORREO = 120

# Validacion por lista blanca (OWASP): en lugar de buscar los caracteres
# peligrosos, se acepta solo lo que un correo necesita. Parte local con
# letras sin tilde, numeros y . _ - +; una sola @; dominio con letras,
# numeros y guiones, separado por puntos y terminado en una extension de
# letras. Fuera quedan espacios, comillas, < > ; = / \ ( ), tildes y la ñ.
# Va sin anclas porque tambien es el atributo `pattern` del HTML, que el
# navegador ancla solo; el `\-` dentro de las clases lo exige el modo `v` con
# el que los navegadores actuales compilan ese atributo.
PATRON_CORREO = r"[A-Za-z0-9._+\-]+@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}"
_REGEX_CORREO = re.compile(rf"(?:{PATRON_CORREO})\Z")
MENSAJE_CORREO = "Use solo letras sin tilde, numeros, los simbolos . _ - + y una @."


def _recortar(valor):
    """Los espacios de los extremos se descartan antes de validar: suelen
    llegar al copiar y pegar el correo, y no son parte de el."""
    return valor.strip() if isinstance(valor, str) else valor


def _lista_blanca_correo(formulario, campo):
    # StopValidation y no ValidationError: corta la cadena para que `Email`
    # no agregue un segundo mensaje sobre el mismo valor.
    if campo.data and not _REGEX_CORREO.match(campo.data):
        raise StopValidation(MENSAJE_CORREO)


def campo_correo(etiqueta="Correo electronico", obligatorio=None):
    """Campo de correo con la lista blanca, comun a todos los formularios.

    Se usa en el login, en las cuentas de acceso y en los clientes: si el alta
    aceptara un correo que el login rechaza, esa cuenta no podria entrar.
    `obligatorio` es el mensaje cuando falta; sin el, el campo es opcional.

    El servidor es la validacion que cuenta. En el navegador, `data-correo`
    hace que el campo descarte al escribir o pegar cualquier caracter fuera de
    la lista (ver base.html): asi ni siquiera se pueden introducir.
    """
    validadores = [DataRequired(obligatorio)] if obligatorio else [Optional()]
    validadores += [
        Length(max=LONGITUD_MAXIMA_CORREO,
               message=f"Maximo {LONGITUD_MAXIMA_CORREO} caracteres."),
        _lista_blanca_correo,
        Email("Correo no valido."),
    ]
    return StringField(
        etiqueta,
        filters=[_recortar],
        validators=validadores,
        render_kw={
            "maxlength": LONGITUD_MAXIMA_CORREO,
            "pattern": PATRON_CORREO,
            "title": MENSAJE_CORREO,
            "inputmode": "email",
            "data-correo": True,
        },
    )
