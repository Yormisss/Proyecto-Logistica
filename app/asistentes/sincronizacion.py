"""Crea o actualiza en Retell el agente de voz de cada rol.

Flujo por agente, siguiendo el versionado de Retell (las versiones publicadas
son de solo lectura y las llamadas web usan la ultima publicada):

1. Si ya existe (por su variable en el .env o por su nombre), se lee su ultima
   version. Debe usar un Retell LLM: un *conversation flow* no se puede
   reemplazar desde aqui. Si esa version esta publicada, se crea un borrador
   nuevo a partir de ella.
2. Se crea un Retell LLM nuevo con el prompt, la bienvenida y las funciones.
   Crear uno nuevo, en vez de editar el anterior, evita depender de como
   Retell versiona los LLM: las versiones publicadas anteriores conservan el
   suyo y se puede volver a ellas desde el panel.
3. Se apunta el agente (o el borrador) a ese LLM con idioma, duracion maxima
   y fin por silencio, y se publica la version.

Al cambiar la URL de ngrok basta con volver a ejecutar el comando.
"""

from dataclasses import dataclass

from app.asistentes import configuracion as conf


class ErrorSincronizacion(Exception):
    """El agente de un rol no se pudo sincronizar."""


@dataclass
class Resultado:
    definicion: conf.DefinicionAgente
    agent_id: str = None
    accion: str = None          # "creado", "actualizado" o "encontrado por nombre"
    version: int = None
    funciones: int = 0
    error: str = None

    @property
    def falta_en_env(self):
        return self.accion in ("creado", "encontrado por nombre")


def _buscar_por_nombre(cliente, nombre):
    """agent_id del agente de voz con ese nombre, o None. Evita duplicarlo."""
    clave = None
    while True:
        argumentos = {"limit": 1000}
        if clave:
            argumentos["pagination_key"] = clave
        pagina = cliente.agent.list(**argumentos)
        for item in pagina.items:
            if item.agent_name == nombre and getattr(item, "channel", "voice") == "voice":
                return item.agent_id
        if not getattr(pagina, "has_more", False) or not getattr(pagina, "pagination_key", None):
            return None
        clave = pagina.pagination_key


def _ajustes(definicion, llm, voz):
    ajustes = {
        "agent_name": definicion.nombre,
        "response_engine": {"type": "retell-llm", "llm_id": llm.llm_id, "version": llm.version},
        "language": conf.IDIOMA,
        "max_call_duration_ms": conf.DURACION_MAXIMA_MS,
        "end_call_after_silence_ms": conf.SILENCIO_MAXIMO_MS,
    }
    if voz:
        ajustes["voice_id"] = voz
    return ajustes


def _crear_llm(cliente, definicion, url_publica):
    tools = conf.herramientas(definicion, url_publica)
    llm = cliente.llm.create(
        general_prompt=conf.prompt(definicion),
        begin_message=conf.bienvenida(definicion),
        general_tools=tools,
        default_dynamic_variables=dict(conf.VARIABLES_DINAMICAS),
    )
    return llm, len(tools)


def sincronizar_agente(cliente, definicion, url_publica, config):
    resultado = Resultado(definicion)
    voz = (config.get("RETELL_VOZ_ID") or "").strip()
    agent_id = (config.get(definicion.variable) or "").strip()
    resultado.accion = "actualizado"
    if not agent_id:
        agent_id = _buscar_por_nombre(cliente, definicion.nombre)
        resultado.accion = "encontrado por nombre" if agent_id else "creado"

    if agent_id:
        actual = cliente.agent.retrieve(agent_id)
        motor = getattr(actual.response_engine, "type", None)
        if motor != "retell-llm":
            raise ErrorSincronizacion(
                f"el agente {agent_id} usa un motor '{motor}', no un Retell LLM; no se puede "
                "reemplazar por API. Configúrelo a mano con docs/configuracion_retell.md o "
                "cree uno nuevo vaciando su variable."
            )
        version = actual.version
        if actual.is_published:
            version = cliente.agent.create_version(agent_id, base_version=actual.version).version
        llm, resultado.funciones = _crear_llm(cliente, definicion, url_publica)
        cliente.agent.update(agent_id, **_ajustes(definicion, llm, voz))
    else:
        if not voz:
            raise ErrorSincronizacion(
                "para crear el agente defina RETELL_VOZ_ID con una voz en español "
                "(panel de Retell, Voices)."
            )
        llm, resultado.funciones = _crear_llm(cliente, definicion, url_publica)
        creado = cliente.agent.create(**_ajustes(definicion, llm, voz))
        agent_id, version = creado.agent_id, creado.version

    cliente.agent.publish(agent_id, version=version)
    resultado.agent_id, resultado.version = agent_id, version
    return resultado


def sincronizar_asistentes(cliente, url_publica, config):
    """Sincroniza los cuatro agentes; un error en uno no detiene a los demas."""
    url = conf.normalizar_url_publica(url_publica)
    resultados = []
    for definicion in conf.AGENTES:
        try:
            resultados.append(sincronizar_agente(cliente, definicion, url, config))
        except Exception as error:
            resultados.append(Resultado(definicion, error=str(error)))
    return resultados
