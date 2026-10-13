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

Toda accion confirmada queda en la auditoria (con origen "voz"), haya salido
bien o no: `ejecutar` la registra despues de la funcion, en su propio commit.

Cada funcion se declara con `funcion_asistente`, que registra su espacio,
nombre, roles permitidos, descripcion y parametros (JSON schema). Las acciones
sobre una misma entidad se agrupan en una sola funcion con el parametro
`operacion` (`funcion_agrupada`): "pedidos" con crear, editar, anular... Ese
registro es la unica fuente de verdad: la sincronizacion con Retell construye
las herramientas de cada agente a partir de el.

Las respuestas son texto breve en espanol, pensado para leerse en voz alta.
Los errores de negocio responden 200 con el mensaje para que el agente se lo
diga al usuario.
"""

import hashlib
import json
from dataclasses import dataclass, field

from flask import Blueprint, g, jsonify, request

from app.extensions import db
from app.models import RESULTADO_ERROR, RESULTADO_EXITO, VIGENCIA_CONFIRMACION, SesionAsistente
from app.services.asistente import firma_valida, sesion_vigente
from app.services.auditoria import registrar_auditoria
from app.services.notificaciones import ORIGEN_VOZ
from app.services.despacho import NOTA_ASISTENTE  # noqa: F401 (la usan los modulos de cada rol)
from app.tiempo import ahora

asistente_api_bp = Blueprint("asistente_api", __name__)


@dataclass(frozen=True)
class Operacion:
    """Una operacion de una funcion agrupada ("crear" de "pedidos")."""
    nombre: str
    vista: object
    titulo: str          # para la auditoria: "Crear pedido"
    descripcion: str
    modifica: bool = True
    # Si su registro de auditoria envia el correo accion_asistente. No lo
    # envian el flujo del conductor ni las entradas de mercancia: son operacion
    # de alto volumen que ya tiene su historial (eventos y movimientos).
    avisar: bool = True


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
    entidad: str = ""
    # Como en Operacion: si su auditoria envia el correo accion_asistente.
    avisar: bool = True
    # nombre -> Operacion, solo en las funciones agrupadas.
    operaciones: dict = field(default_factory=dict)

    @property
    def ruta(self):
        return f"/api/asistente/{self.espacio}/{self.nombre}"

    @property
    def descripcion_completa(self):
        """Descripcion para Retell: la funcion y, si agrupa, cada operacion."""
        if not self.operaciones:
            return self.descripcion + (
                " Modifica datos: llamala primero sin 'confirmar' para obtener el resumen."
                if self.accion else ""
            )
        detalle = "; ".join(
            f"'{op.nombre}': {op.descripcion}" + (" (modifica datos)" if op.modifica else "")
            for op in self.operaciones.values()
        )
        texto = f"{self.descripcion} Operaciones: {detalle}."
        if any(op.modifica for op in self.operaciones.values()):
            texto += (" Las que modifican datos: llamala primero sin 'confirmar' para obtener "
                      "el resumen.")
        return texto


# (espacio, nombre) -> FuncionAsistente
FUNCIONES = {}


def funcion_asistente(espacio, nombre, *, roles, descripcion, parametros=None,
                      requeridos=(), accion=False, entidad=None, avisar=True):
    """Registra una custom function.

    `parametros` son las propiedades JSON schema de los argumentos y
    `requeridos` los obligatorios. `accion=True` marca las funciones que
    modifican datos: deben llamar a `exigir_confirmacion` antes de cambiar
    nada, y la sincronizacion con Retell les agrega el parametro `confirmar`.
    `entidad` es lo que la accion modifica ("pedido", "producto"...), para la
    auditoria; por omision, la ultima palabra del nombre. `avisar=False`, como
    en las operaciones, deja la accion en la auditoria sin el correo.
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
            entidad=entidad or nombre.rsplit("-", 1)[-1],
            avisar=avisar,
        )
        return vista

    return registrar


def funcion_agrupada(espacio, nombre, *, roles, descripcion, entidad, parametros=None):
    """Registra una funcion que agrupa las operaciones de una entidad.

    Devuelve el decorador de cada operacion:

        pedidos = funcion_agrupada("gestor", "pedidos", roles=..., entidad="pedido", ...)

        @pedidos("crear", "Crear pedido", "Crea un pedido para ...")
        def crear_pedido_voz(): ...

    `operacion` es un parametro mas: entra en la huella de la confirmacion, de
    modo que confirmar "editar" no ejecuta "anular". `modifica=False` marca las
    operaciones de solo consulta, que no piden confirmacion. Las operaciones
    que se declaran en otro modulo agregan sus parametros con
    `decorador.agregar_parametros({...})`.
    """
    parametros = {
        "operacion": {"type": "string", "enum": [], "description": "Que hacer."},
        **(parametros or {}),
    }

    def despachar():
        funcion = FUNCIONES[(espacio, nombre)]
        elegida = str(g.argumentos.get("operacion") or "").strip().lower()
        operacion = funcion.operaciones.get(elegida)
        if operacion is None:
            from app.services.busqueda_voz import enumerar
            return responder(f"¿Qué quieres hacer? Con {nombre} puedo: "
                             f"{enumerar(list(funcion.operaciones))}.")
        g.operacion = operacion
        return operacion.vista()

    funcion_asistente(espacio, nombre, roles=roles, descripcion=descripcion, parametros=parametros,
                      requeridos=("operacion",), accion=True, entidad=entidad)(despachar)
    funcion = FUNCIONES[(espacio, nombre)]

    def operacion(nombre_operacion, titulo, descripcion_operacion, *, modifica=True, avisar=True):
        def registrar(vista):
            if nombre_operacion in funcion.operaciones:
                raise ValueError(f"Operacion duplicada: {espacio}/{nombre}:{nombre_operacion}")
            funcion.operaciones[nombre_operacion] = Operacion(
                nombre_operacion, vista, titulo, descripcion_operacion, modifica, avisar)
            funcion.parametros["operacion"]["enum"].append(nombre_operacion)
            return vista
        return registrar

    def agregar_parametros(nuevos):
        for clave, esquema in nuevos.items():
            if clave in funcion.parametros:
                raise ValueError(f"Parametro duplicado en {espacio}/{nombre}: {clave}")
            funcion.parametros[clave] = esquema

    operacion.agregar_parametros = agregar_parametros
    return operacion


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
    g.origen_accion = ORIGEN_VOZ
    g.argumentos = datos.get("args") if isinstance(datos.get("args"), dict) else {}


@asistente_api_bp.route("/<espacio>/<nombre>", methods=["POST"])
def ejecutar(espacio, nombre):
    try:
        respuesta = g.funcion.vista()
    except Exception:
        db.session.rollback()
        if g.get("accion_confirmada"):
            _auditar(RESULTADO_ERROR, "Error interno al ejecutar la accion.")
        raise
    if g.get("accion_confirmada"):
        _auditar(_resultado_confirmada(), (respuesta.get_json(silent=True) or {}).get("mensaje"))
    return respuesta


def _resultado_confirmada():
    """Si la accion confirmada quedo hecha.

    La confirmacion se consume en la misma transaccion que la accion: si la
    funcion hizo commit, ya no esta pendiente; si hizo rollback (o no llego a
    confirmar), sigue ahi.
    """
    db.session.rollback()
    pendiente = (
        db.session.query(SesionAsistente.confirmacion_firma)
        .filter(SesionAsistente.id == g.sesion.id)
        .scalar()
    )
    return RESULTADO_ERROR if pendiente == g.accion_confirmada["firma"] else RESULTADO_EXITO


def _auditar(resultado, mensaje):
    funcion = g.funcion
    operacion = g.get("operacion")
    argumentos = {k: v for k, v in g.argumentos.items() if k != "confirmar"}
    registrar_auditoria(
        operacion.titulo if operacion else funcion.nombre.replace("-", " ").capitalize(),
        funcion.entidad,
        g.accion_confirmada["resumen"],
        {"funcion": f"{funcion.espacio}/{funcion.nombre}"
                    + (f":{operacion.nombre}" if operacion else ""),
         "datos": argumentos, "respuesta": mensaje},
        resultado=resultado,
        usuario=g.usuario,
        origen=ORIGEN_VOZ,
        call_id=g.sesion.call_id,
        avisar=operacion.avisar if operacion else funcion.avisar,
    )
    db.session.commit()


def responder(mensaje):
    return jsonify(mensaje=mensaje)


def cantidad(numero, singular, plural=None):
    """"1 parada", "3 paradas": el agente lee el texto tal cual."""
    return f"{numero} {singular if numero == 1 else (plural or singular + 's')}"


# --------------------------------------------------------------------------
# Confirmacion de acciones
# --------------------------------------------------------------------------
# Toda funcion que modifica datos se ejecuta en dos llamadas. La primera valida,
# no cambia nada y devuelve un resumen que termina en "¿confirmas?"; el servidor
# guarda en la sesion la huella de la funcion y sus argumentos. La segunda, con
# confirmar=true, solo ejecuta si esa misma huella sigue pendiente y vigente, y
# la consume en la misma transaccion que la accion. Asi no basta con que el LLM
# envie confirmar=true: la accion tuvo que resumirse antes en esta llamada, con
# exactamente los mismos argumentos, hace menos de VIGENCIA_CONFIRMACION, y una
# confirmacion no sirve dos veces.

def _firma_accion():
    argumentos = {k: v for k, v in g.argumentos.items() if k != "confirmar"}
    contenido = json.dumps(
        {"funcion": f"{g.funcion.espacio}/{g.funcion.nombre}", "argumentos": argumentos},
        sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    )
    return hashlib.sha256(contenido.encode("utf-8")).hexdigest()


def _pide_confirmar():
    valor = g.argumentos.get("confirmar")
    return valor is True or (isinstance(valor, str) and valor.strip().lower() == "true")


def exigir_confirmacion(resumen):
    """None si la accion ya fue confirmada; si no, la respuesta con el resumen.

    Uso en una funcion con accion=True, despues de validar y antes de cambiar
    nada:

        pendiente = exigir_confirmacion("Voy a ...")
        if pendiente:
            return pendiente

    Al devolver None la confirmacion ya quedo consumida en la sesion de la base;
    se confirma con el commit de la accion y se revierte con su rollback.
    """
    firma = _firma_accion()
    if _pide_confirmar():
        consumidas = (
            db.session.query(SesionAsistente)
            .filter(
                SesionAsistente.id == g.sesion.id,
                SesionAsistente.confirmacion_firma == firma,
                SesionAsistente.confirmacion_vence_en > ahora(),
            )
            .update(
                {"confirmacion_firma": None, "confirmacion_vence_en": None},
                synchronize_session=False,
            )
        )
        if consumidas == 1:
            # `ejecutar` lo registra en la auditoria al terminar la funcion.
            g.accion_confirmada = {"firma": firma, "resumen": resumen}
            return None

    # Sin confirmar, o con una confirmacion que no corresponde a lo resumido:
    # se (re)emite el resumen y queda pendiente solo esta accion.
    g.sesion.confirmacion_firma = firma
    g.sesion.confirmacion_vence_en = ahora() + VIGENCIA_CONFIRMACION
    db.session.commit()
    return responder(f"{resumen} ¿confirmas?")


# Registro de las funciones de cada rol (importan los decoradores de arriba).
from app.controllers.asistente_api import (  # noqa: E402,F401
    admin, administracion, cliente, conductor, gestor, maestros,
)
