"""Configuracion de cada agente de voz: prompt, bienvenida y herramientas.

El prompt y la bienvenida de cada rol viven en `prompts/<rol>.md`; las reglas
comunes (confirmacion, fechas, no inventar datos) en `prompts/comun.md`. Las
herramientas no se escriben a mano: salen del registro de funciones del
asistente, de modo que lo que Retell puede llamar y lo que el servidor acepta
nunca se desalinean.

`generar_documento` produce `docs/configuracion_retell.md`, la referencia para
revisar la configuracion o cargarla a mano en el panel de Retell.
"""

from dataclasses import dataclass
from pathlib import Path

from app.controllers.asistente_api import funciones_de_rol
from app.models import Rol
from app.services.asistente import AGENTES_POR_ROL

CARPETA_PROMPTS = Path(__file__).resolve().parent / "prompts"

# Ajustes de llamada comunes a todos los agentes.
IDIOMA = "es-419"                     # espanol latinoamericano
DURACION_MAXIMA_MS = 5 * 60 * 1000    # la sesion del servidor dura 10 minutos
SILENCIO_MAXIMO_MS = 20 * 1000        # cuelga tras 20 s sin que el usuario hable
TIEMPO_FUNCION_MS = 15 * 1000         # las funciones responden en milisegundos

# Variables dinamicas que el servidor envia al crear la llamada.
VARIABLES_DINAMICAS = {"nombre_usuario": "", "fecha_hoy": ""}

DESCRIPCION_CONFIRMAR = (
    "Solo true despues de leerle al usuario el resumen que termino en '¿confirmas?' y de que "
    "dijera que si; con exactamente los mismos argumentos de ese resumen."
)


@dataclass(frozen=True)
class DefinicionAgente:
    clave: str        # nombre del archivo de prompt
    rol: str
    nombre: str       # agent_name en Retell; tambien sirve para encontrarlo

    @property
    def variable(self):
        """Variable de entorno con el agent_id (la misma que usa la app)."""
        return AGENTES_POR_ROL[self.rol]


AGENTES = (
    DefinicionAgente("conductor", Rol.CONDUCTOR, "SGDS - Conductor"),
    DefinicionAgente("gestor", Rol.DESPACHADOR, "SGDS - Gestor logistico"),
    DefinicionAgente("admin", Rol.ADMIN, "SGDS - Administrador"),
    DefinicionAgente("cliente", Rol.CLIENTE, "SGDS - Cliente"),
)


def _leer_prompt(clave):
    """(bienvenida, prompt) del archivo `prompts/<clave>.md`."""
    contenido = (CARPETA_PROMPTS / f"{clave}.md").read_text(encoding="utf-8")
    cabecera, separador, cuerpo = contenido.partition("\n---\n")
    if not separador or not cabecera.startswith("bienvenida:"):
        raise ValueError(f"prompts/{clave}.md debe empezar con 'bienvenida: ...' y una linea '---'")
    return cabecera[len("bienvenida:"):].strip(), cuerpo.strip()


def bienvenida(definicion):
    return _leer_prompt(definicion.clave)[0]


def prompt(definicion):
    """Prompt del rol seguido de las reglas comunes."""
    comun = (CARPETA_PROMPTS / "comun.md").read_text(encoding="utf-8").strip()
    return f"{_leer_prompt(definicion.clave)[1]}\n\n{comun}\n"


def parametros(funcion):
    """JSON schema de los argumentos; las acciones suman `confirmar`."""
    propiedades = dict(funcion.parametros)
    if funcion.accion:
        propiedades["confirmar"] = {"type": "boolean", "description": DESCRIPCION_CONFIRMAR}
    esquema = {"type": "object", "properties": propiedades}
    if funcion.requeridos:
        esquema["required"] = list(funcion.requeridos)
    return esquema


def herramientas(definicion, url_publica):
    """Custom functions del agente, con la URL completa de cada una.

    POST, con los argumentos anidados en "args" (args_at_root=False): el
    servidor necesita el objeto "call" del cuerpo para leer el call_id.
    """
    funciones = funciones_de_rol(definicion.rol)
    nombres = [f.nombre for f in funciones]
    if len(nombres) != len(set(nombres)):
        raise ValueError(f"Nombres de funcion repetidos para el rol {definicion.rol}")
    return [
        {
            "type": "custom",
            "name": funcion.nombre,
            "description": funcion.descripcion
            + (" Modifica datos: llamala primero sin 'confirmar' para obtener el resumen."
               if funcion.accion else ""),
            "url": f"{url_publica}{funcion.ruta}",
            "method": "POST",
            "parameters": parametros(funcion),
            "args_at_root": False,
            "speak_during_execution": False,
            "speak_after_execution": True,
            "timeout_ms": TIEMPO_FUNCION_MS,
        }
        for funcion in funciones
    ]


def normalizar_url_publica(valor):
    """URL base publica (ngrok o Render), https y sin barra final."""
    url = (valor or "").strip().rstrip("/")
    if not url:
        raise ValueError("Defina URL_PUBLICA con la URL https publica del servidor (ngrok o Render).")
    if not url.startswith("https://"):
        raise ValueError("URL_PUBLICA debe empezar por https://: Retell solo llama a URLs seguras.")
    return url


# --------------------------------------------------------------------------
# Documento de referencia
# --------------------------------------------------------------------------

def generar_documento():
    """Markdown con la configuracion completa de los cuatro agentes."""
    import json

    lineas = [
        "# Configuración de los agentes de Retell",
        "",
        "Generado con `flask --app run documentar-asistentes` a partir de `app/asistentes/` y del",
        "registro de funciones. No lo edite a mano: cambie los prompts o las funciones y vuelva a",
        "generarlo. `flask --app run sincronizar-asistentes` aplica esta misma configuración por API;",
        "este documento sirve para revisarla o para cargarla a mano en el panel de Retell.",
        "",
        "Ajustes comunes de cada agente:",
        "",
        f"- Idioma: `{IDIOMA}` (español latinoamericano).",
        f"- Duración máxima de la llamada: {DURACION_MAXIMA_MS // 60000} minutos "
        f"(`max_call_duration_ms = {DURACION_MAXIMA_MS}`).",
        f"- Fin de la llamada tras {SILENCIO_MAXIMO_MS // 1000} segundos de silencio "
        f"(`end_call_after_silence_ms = {SILENCIO_MAXIMO_MS}`).",
        "- Motor: Retell LLM. Variables dinámicas: `nombre_usuario` y `fecha_hoy` (las envía el",
        "  servidor al crear la llamada).",
        "- Funciones: método `POST`, URL `<URL_PUBLICA>` + la ruta indicada, con la opción de enviar",
        "  solo los argumentos **desactivada** (`args_at_root: false`): el servidor necesita el objeto",
        "  `call` del cuerpo para leer el `call_id`.",
        "- Después de cambiar un agente existente, publique la nueva versión: las llamadas web usan",
        "  la última versión publicada.",
        "",
    ]
    for definicion in AGENTES:
        lineas += [
            f"## {definicion.nombre}",
            "",
            f"- Rol: `{definicion.rol}`. Variable con el `agent_id`: `{definicion.variable}`.",
            "",
            "### Mensaje de bienvenida",
            "",
            "```text",
            bienvenida(definicion),
            "```",
            "",
            "### Prompt",
            "",
            "```markdown",
            prompt(definicion).rstrip(),
            "```",
            "",
            "### Funciones",
            "",
            "| Función | Ruta | Descripción |",
            "|---|---|---|",
        ]
        tools = herramientas(definicion, "<URL_PUBLICA>")
        for tool in tools:
            ruta = tool["url"].replace("<URL_PUBLICA>", "")
            lineas.append(f"| `{tool['name']}` | `{ruta}` | {tool['description']} |")
        lineas += ["", "Parámetros (JSON schema) de cada función:", ""]
        for tool in tools:
            lineas += [
                f"`{tool['name']}`:",
                "",
                "```json",
                json.dumps(tool["parameters"], ensure_ascii=False, indent=2),
                "```",
                "",
            ]
    return "\n".join(lineas).rstrip() + "\n"
