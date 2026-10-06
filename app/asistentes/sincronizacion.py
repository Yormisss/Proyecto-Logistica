"""Crea o actualiza en Retell el agente de voz de cada rol.

Versionado de Retell: las versiones publicadas son de solo lectura, las
llamadas web usan la ultima publicada, y el agente y su Retell LLM comparten
el numero de version (la version N del agente usa la version N de su LLM).
Al crear el borrador N+1 de un agente, Retell crea tambien la version N+1 de
su LLM; apuntar el borrador a otro LLM, o a otra version, responde 400
"Response engine version must match agent version".

Flujo por agente:

1. Si ya existe (por su variable en el .env o por su nombre), se lee su ultima
   version. Debe usar un Retell LLM: un *conversation flow* no se puede
   configurar desde aqui. Si esa version esta publicada, se crea un borrador
   a partir de ella; si ya es un borrador (por ejemplo, de un intento que
   fallo), se reutiliza.
2. Se actualiza el LLM del borrador, en la version del borrador, con el
   prompt, la bienvenida y las funciones; y el borrador con idioma, duracion
   maxima y fin por silencio, sin tocar su response_engine.
3. Se publica el borrador. Las versiones anteriores quedan intactas, con su
   version del LLM, y se puede volver a ellas desde el panel.

Un agente nuevo se crea en la version 0 con un Retell LLM nuevo, tambien en la
version 0. Al cambiar la URL de ngrok basta con volver a ejecutar el comando.

"""

from dataclasses import dataclass

from app.asistentes import configuracion as conf

# Timeout de cada llamada a Retell durante la sincronizacion, en segundos. El
# boton del asistente usa 10; aqui nadie espera en vivo y Retell a veces tarda.
TIMEOUT_SINCRONIZACION = 60
# Reintentos de un agente cuyo intento termino en "Request timed out".
REINTENTOS_POR_TIMEOUT = 1


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
    reintentos: int = 0

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


def _ajustes(definicion, voz):
    """Ajustes de llamada del agente (sin el response_engine)."""
    ajustes = {
        "agent_name": definicion.nombre,
        "language": conf.IDIOMA,
        "max_call_duration_ms": conf.DURACION_MAXIMA_MS,
        "end_call_after_silence_ms": conf.SILENCIO_MAXIMO_MS,
    }
    if voz:
        ajustes["voice_id"] = voz
    return ajustes


def _configuracion_llm(definicion, url_publica):
    tools = conf.herramientas(definicion, url_publica)
    return {
        "general_prompt": conf.prompt(definicion),
        "begin_message": conf.bienvenida(definicion),
        "general_tools": tools,
        "default_dynamic_variables": dict(conf.VARIABLES_DINAMICAS),
    }


def sincronizar_agente(cliente, definicion, url_publica, config):
    resultado = Resultado(definicion)
    voz = (config.get("RETELL_VOZ_ID") or "").strip()
    agent_id = (config.get(definicion.variable) or "").strip()
    resultado.accion = "actualizado"
    if not agent_id:
        agent_id = _buscar_por_nombre(cliente, definicion.nombre)
        resultado.accion = "encontrado por nombre" if agent_id else "creado"

    llm = _configuracion_llm(definicion, url_publica)
    resultado.funciones = len(llm["general_tools"])

    if agent_id:
        actual = cliente.agent.retrieve(agent_id)
        motor = getattr(actual.response_engine, "type", None)
        if motor != "retell-llm":
            raise ErrorSincronizacion(
                f"el agente {agent_id} usa un motor '{motor}', no un Retell LLM; no se puede "
                "configurar por API. Configúrelo a mano con docs/configuracion_retell.md o "
                "cree uno nuevo vaciando su variable."
            )
        if actual.is_published:
            borrador = cliente.agent.create_version(agent_id, base_version=actual.version)
            actual = cliente.agent.retrieve(agent_id, version=borrador.version)
        version = actual.version
        # El borrador ya trae su propia version del LLM (la misma que la suya).
        llm_id = actual.response_engine.llm_id
        llm_version = actual.response_engine.version
        llm_version = int(llm_version) if llm_version is not None else version
        cliente.llm.update(llm_id, version=llm_version, **llm)
        cliente.agent.update(agent_id, version=version, **_ajustes(definicion, voz))
    else:
        if not voz:
            raise ErrorSincronizacion(
                "para crear el agente defina RETELL_VOZ_ID con una voz en español "
                "(panel de Retell, Voices)."
            )
        nuevo_llm = cliente.llm.create(**llm)
        creado = cliente.agent.create(
            response_engine={"type": "retell-llm", "llm_id": nuevo_llm.llm_id,
                             "version": nuevo_llm.version},
            **_ajustes(definicion, voz),
        )
        agent_id, version = creado.agent_id, creado.version

    cliente.agent.publish(agent_id, version=version)
    resultado.agent_id, resultado.version = agent_id, version
    return resultado


def _es_timeout(error):
    from retell import APITimeoutError

    return isinstance(error, APITimeoutError) or "Request timed out" in str(error)


def sincronizar_asistentes(cliente, url_publica, config, roles=None):
    """Sincroniza los agentes (todos, o solo los de `roles` por su clave).

    Un error en uno no detiene a los demas. Si un intento termina en "Request
    timed out", se repite la sincronizacion completa de ese agente (hasta
    REINTENTOS_POR_TIMEOUT veces). Es seguro: `sincronizar_agente` vuelve a
    leer el agente en Retell, asi que si la llamada que agoto el tiempo si se
    aplico (un borrador ya creado, una version ya publicada) el reintento parte
    de ese estado en vez de duplicarlo.
    """
    url = conf.normalizar_url_publica(url_publica)
    resultados = []
    for definicion in conf.AGENTES:
        if roles and definicion.clave not in roles:
            continue
        intentos = 0
        while True:
            try:
                resultado = sincronizar_agente(cliente, definicion, url, config)
            except Exception as error:
                if _es_timeout(error) and intentos < REINTENTOS_POR_TIMEOUT:
                    intentos += 1
                    continue
                resultado = Resultado(definicion, error=str(error))
            resultado.reintentos = intentos
            resultados.append(resultado)
            break
    return resultados
