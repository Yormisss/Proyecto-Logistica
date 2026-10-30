"""Autorizacion por permisos (RF1 / RNF5).

Cada pantalla exige un permiso (`requiere_permiso`); los permisos de cada
cuenta salen de sus roles y ajustes (app/services/permisos.py). Evitan que
personal no autorizado acceda a las rutas de entrega o altere el inventario.
"""

from functools import wraps
from urllib.parse import urlsplit

from flask import abort, request, url_for
from flask_login import current_user
from wtforms import StringField
from wtforms.validators import (
    DataRequired, Email, Length, Optional, StopValidation, ValidationError,
)

from app.models import Rol
from app.permisos import roles_por_defecto
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

# Si los permisos de la cuenta no le dan la pagina de su rol, la primera de
# estas que si pueda abrir.
INICIOS_ALTERNATIVOS = (
    ("tablero.ver", "admin.dashboard"),
    ("conductor.ruta", "conductor.mi_ruta"),
    ("portal.cliente", "cliente.mis_pedidos"),
    ("pedidos.ver", "pedidos.lista"),
    ("rutas.ver", "rutas.lista"),
    ("inventario.ver", "inventario.lista"),
    ("clientes.ver", "clientes_admin.lista"),
    ("vehiculos.ver", "vehiculos.lista"),
    ("solicitudes.ver", "solicitudes.lista"),
    ("analitica.ver", "admin.analitica_operacion"),
    ("bigdata.ver", "bigdata.panel"),
    ("bitacora.ver", "bitacora.lista"),
    ("errores.ver", "errores.lista"),
    ("rendimiento.ver", "admin.rendimiento"),
    ("configuracion.gestionar", "configuracion.inicio"),
)

# Destino de reserva para una cuenta sin ninguna pagina: el perfil es accesible
# a cualquier sesion autenticada, asi que nunca produce un bucle de redirecciones.
INICIO_POR_DEFECTO = "auth.perfil"


def destino_por_rol(usuario):
    """URL de aterrizaje: la de su rol (tablero, ruta del dia o portal) si sus
    permisos se la dan; si no, la primera pagina que puede abrir."""
    inicio = INICIO_POR_ROL.get(usuario.rol)
    permiso = getattr(_vista(inicio), "permiso", None) if inicio else None
    if inicio and permiso and usuario.puede(permiso):
        return url_for(inicio)
    for permiso, endpoint in INICIOS_ALTERNATIVOS:
        if usuario.puede(permiso):
            return url_for(endpoint)
    return url_for(INICIO_POR_DEFECTO)


def _vista(endpoint):
    from flask import current_app

    return current_app.view_functions.get(endpoint)


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


def requiere_permiso(permiso, *, post=None):
    """La vista exige `permiso`; con `post`, un POST exige ese otro.

    `post` es para las pantallas que muestran (ver) y en la misma ruta
    guardan (gestionar), como el detalle de un producto con su formulario de
    movimiento.
    """
    def decorador(vista):
        @wraps(vista)
        def envoltura(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            exigido = post if post and request.method == "POST" else permiso
            if not current_user.puede(exigido):
                abort(403)
            return vista(*args, **kwargs)

        # Para inspeccionar quien puede usar cada pantalla (prueba de cobertura
        # por voz y pagina de inicio). `login_required` los conserva:
        # functools.wraps copia __dict__. `roles_permitidos` son los roles del
        # sistema que, en una base nueva, pueden guardar en ella.
        envoltura.permiso = permiso
        envoltura.permiso_post = post or permiso
        envoltura.roles_permitidos = roles_por_defecto(post or permiso)
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
