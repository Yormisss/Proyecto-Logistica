"""Decoradores de autorizacion por rol (RF1 / RNF5).

Evitan que personal no autorizado acceda a las rutas de entrega o altere el inventario.
"""

from functools import wraps
from urllib.parse import urlsplit

from flask import abort, url_for
from flask_login import current_user
from wtforms import StringField
from wtforms.validators import (
    DataRequired, Email, Length, Optional, StopValidation, ValidationError,
)

from app.models import Rol
from app.services.usuarios import error_politica
from app.services.validacion import MENSAJE_CORREO, PATRON_CORREO, correo_en_lista_blanca

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

        # Para inspeccionar quien puede usar cada pantalla (prueba de cobertura
        # por voz). `login_required` lo conserva: functools.wraps copia __dict__.
        envoltura.roles_permitidos = tuple(roles)
        return envoltura

    return decorador


# --------------------------------------------------------------------------
# Correo electronico: lista blanca de caracteres (RNF5)
# --------------------------------------------------------------------------

# La de `usuarios.correo`, `clientes.correo` y `solicitudes_contacto.correo`.
LONGITUD_MAXIMA_CORREO = 120

# El patron y su mensaje viven en app.services.validacion, que tambien usa el
# asistente de voz: una sola lista blanca para la pantalla y la voz.


def _recortar(valor):
    """Los espacios de los extremos se descartan antes de validar: suelen
    llegar al copiar y pegar el correo, y no son parte de el."""
    return valor.strip() if isinstance(valor, str) else valor


def _lista_blanca_correo(formulario, campo):
    # StopValidation y no ValidationError: corta la cadena para que `Email`
    # no agregue un segundo mensaje sobre el mismo valor.
    if campo.data and not correo_en_lista_blanca(campo.data):
        raise StopValidation(MENSAJE_CORREO)


def campo_correo(etiqueta="Correo electrónico", obligatorio=None, validar=True):
    """Campo de correo con la lista blanca, comun a todos los formularios.

    Se usa en el login, en las cuentas de acceso y en los clientes: si el alta
    aceptara un correo que el login rechaza, esa cuenta no podria entrar.
    `obligatorio` es el mensaje cuando falta; sin el, el campo es opcional.
    `validar=False` es para los formularios cuyas reglas aplica un servicio
    (cuentas y clientes): `Validador.correo` usa la misma lista blanca, tambien
    por voz, y el campo solo aporta el filtro del navegador.

    El servidor es la validacion que cuenta. En el navegador, `data-correo`
    hace que el campo descarte al escribir o pegar cualquier caracter fuera de
    la lista (ver base.html): asi ni siquiera se pueden introducir.
    """
    validadores = [DataRequired(obligatorio)] if obligatorio else [Optional()]
    if validar:
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


# --------------------------------------------------------------------------
# Contrasena: politica de complejidad (RNF5)
# --------------------------------------------------------------------------

def politica_contrasena(formulario, campo):
    """Validador de WTForms con la politica de `app.services.usuarios`, la misma
    que aplica el alta de cuentas."""
    if campo.data and (error := error_politica(campo.data)):
        raise ValidationError(error)
