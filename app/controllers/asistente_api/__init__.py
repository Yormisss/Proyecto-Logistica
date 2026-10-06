"""Custom functions de Retell para el asistente de voz de cada rol.

Retell llama a estos endpoints desde sus servidores durante la conversacion,
sin cookie de sesion ni token CSRF (el blueprint esta exento). Todas pasan por
la misma ruta, /api/asistente/<espacio>/<funcion>, y por un unico
`before_request` que, antes de llegar a la funcion:

  1. comprueba que la funcion exista en el registro (404 si no);
  2. verifica la firma X-Retell-Signature sobre el cuerpo crudo, que prueba
     que la peticion viene de Retell (401 si falta o no es valida);
  3. identifica al usuario por el `call.call_id` del cuerpo en
     `sesiones_asistente` (403 si no existe, vencio o el usuario fue
     desactivado o cambio de rol);
  4. comprueba que el rol de la sesion este entre los que la funcion declara
     (403 si no: una sesion de un rol no puede llamar funciones de otro).

Cada funcion se declara con `funcion_asistente`, que registra su espacio,
nombre, roles permitidos, descripcion y parametros (JSON schema). Ese registro
es la unica fuente de verdad: la sincronizacion con Retell construye las
herramientas de cada agente a partir de el.

Las respuestas son texto breve en espanol, pensado para leerse en voz alta.
Los errores de negocio responden 200 con el mensaje para que el agente se lo
diga al usuario.
"""

from dataclasses import dataclass, field

from flask import Blueprint, g, jsonify, request

from app.services.asistente import firma_valida, sesion_vigente

asistente_api_bp = Blueprint("asistente_api", __name__)

NOTA_ASISTENTE = "Registrado por el asistente de voz"


@dataclass(frozen=True)
class FuncionAsistente:
    espacio: str
    nombre: str
    vista: object
    roles: tuple
    descripcion: str
    parametros: dict = field(default_factory=dict)
    requeridos: tuple = ()
    accion: bool = False

    @property
    def ruta(self):
        return f"/api/asistente/{self.espacio}/{self.nombre}"


# (espacio, nombre) -> FuncionAsistente
FUNCIONES = {}


def funcion_asistente(espacio, nombre, *, roles, descripcion, parametros=None,
                      requeridos=(), accion=False):
    """Registra una custom function.

    `parametros` son las propiedades JSON schema de los argumentos y
    `requeridos` los obligatorios. `accion=True` marca las funciones que
    modifican datos: exigen confirmacion (ver `confirmacion.py`).
    """
    def registrar(vista):
        clave = (espacio, nombre)
        if clave in FUNCIONES:
            raise ValueError(f"Funcion del asistente duplicada: {espacio}/{nombre}")
        FUNCIONES[clave] = FuncionAsistente(
            espacio=espacio,
            nombre=nombre,
            vista=vista,
            roles=tuple(roles),
            descripcion=descripcion,
            parametros=dict(parametros or {}),
            requeridos=tuple(requeridos),
            accion=accion,
        )
        return vista

    return registrar


def funciones_de_rol(rol):
    """Funciones que puede llamar una sesion del rol, en orden de registro."""
    return [f for f in FUNCIONES.values() if rol in f.roles]


@asistente_api_bp.before_request
def autenticar_llamada():
    argumentos_ruta = request.view_args or {}
    funcion = FUNCIONES.get((argumentos_ruta.get("espacio"), argumentos_ruta.get("nombre")))
    if funcion is None:
        return jsonify(error="Funcion inexistente."), 404

    cuerpo = request.get_data(as_text=True)
    if not firma_valida(cuerpo, request.headers.get("X-Retell-Signature")):
        return jsonify(error="Firma invalida."), 401

    datos = request.get_json(silent=True)
    if not isinstance(datos, dict):
        datos = {}
    llamada = datos.get("call") if isinstance(datos.get("call"), dict) else {}

    sesion = sesion_vigente(llamada.get("call_id"))
    if sesion is None:
        return jsonify(error="Llamada no autorizada."), 403
    if sesion.rol not in funcion.roles:
        return jsonify(error="Funcion no permitida para este rol."), 403

    g.sesion = sesion
    g.usuario = sesion.usuario
    g.funcion = funcion
    g.argumentos = datos.get("args") if isinstance(datos.get("args"), dict) else {}


@asistente_api_bp.route("/<espacio>/<nombre>", methods=["POST"])
def ejecutar(espacio, nombre):
    return g.funcion.vista()


def responder(mensaje):
    return jsonify(mensaje=mensaje)


def cantidad(numero, singular, plural=None):
    """"1 parada", "3 paradas": el agente lee el texto tal cual."""
    return f"{numero} {singular if numero == 1 else (plural or singular + 's')}"


# Registro de las funciones de cada rol (importan los decoradores de arriba).
from app.controllers.asistente_api import conductor  # noqa: E402,F401
