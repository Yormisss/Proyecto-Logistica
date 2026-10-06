"""Configuracion de los agentes y comando `sincronizar-asistentes`.

Corre sin internet con un Retell simulado que imita su versionado: una version
publicada no se puede editar, solo se publican borradores y las llamadas web
usan la ultima publicada. Ademas, cada llamada se valida contra la firma real
del SDK de Retell, para que un argumento mal escrito no pase desapercibido.
"""

import inspect
import pathlib
import re
import sys
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from retell.resources.agent import AgentResource
from retell.resources.llm import LlmResource

from run import app
from app.asistentes import configuracion as conf
from app.controllers.asistente_api import FUNCIONES, funciones_de_rol
from app.models import Rol
from app.services import asistente as servicio_asistente

fallos = []
def check(c, m):
    print(("  OK   " if c else "  FALLA") + f" {m}")
    if not c: fallos.append(m)

RAIZ = pathlib.Path(__file__).resolve().parent.parent
URL = "https://abcd-1234.ngrok-free.app"
VARIABLES = ("RETELL_AGENTE_CONDUCTOR_ID", "RETELL_AGENTE_GESTOR_ID", "RETELL_AGENTE_ADMIN_ID",
             "RETELL_AGENTE_CLIENTE_ID")


# ---- Retell simulado ----
# Imita el versionado real, verificado contra la cuenta de Retell: el agente y
# su Retell LLM comparten el numero de version; crear el borrador N+1 de un
# agente crea tambien la version N+1 de su LLM; publicar el agente publica esa
# version del LLM; una version publicada no se edita; y apuntar un agente a un
# LLM en otra version responde el mismo 400 que Retell.
ERROR_VERSION = ("Error code: 400 - {'status': 'error', 'message': "
                 "'Response engine version must match agent version'}")

class ErrorRetell(Exception):
    pass

def _parametros(metodo):
    return {n for n in inspect.signature(metodo).parameters if n != "self"}

class Recurso:
    def __init__(self, retell, firmas):
        self.retell, self.firmas = retell, firmas
    def _validar(self, metodo, argumentos):
        extra = set(argumentos) - self.firmas[metodo]
        if extra:
            raise TypeError(f"{metodo} no acepta {sorted(extra)}")

class LlmSimulado(Recurso):
    def create(self, **kw):
        self._validar("create", kw)
        llm_id = f"llm_{len(self.retell.llms) + 1}"
        self.retell.llms[llm_id] = {0: {**kw, "publicada": False}}
        self.retell.registro.append(("llm.create", llm_id))
        return SimpleNamespace(llm_id=llm_id, version=0)
    def update(self, llm_id, **kw):
        self._validar("update", {"llm_id": llm_id, **kw})
        versiones = self.retell.llms[llm_id]
        version = kw.pop("version", max(versiones))
        if versiones[version]["publicada"]:
            raise ErrorRetell("Error code: 400 - una version publicada del LLM no se puede editar")
        versiones[version].update(kw)
        self.retell.registro.append(("llm.update", llm_id, version))

class AgenteSimulado(Recurso):
    def _version(self, agent_id, version=None):
        versiones = self.retell.agentes[agent_id]
        return versiones[-1] if version is None else next(v for v in versiones if v["version"] == version)
    def list(self, **kw):
        self._validar("list", kw)
        return SimpleNamespace(
            items=[SimpleNamespace(agent_id=a, agent_name=v[-1]["agent_name"], channel="voice")
                   for a, v in self.retell.agentes.items()],
            has_more=False, pagination_key=None)
    def retrieve(self, agent_id, **kw):
        self._validar("retrieve", {"agent_id": agent_id, **kw})
        v = self._version(agent_id, kw.get("version"))
        motor = dict(v["response_engine"])
        if "version" in motor:
            motor["version"] = float(motor["version"])     # Retell la devuelve como 1.0
        return SimpleNamespace(agent_id=agent_id, version=v["version"], is_published=v["publicada"],
                               agent_name=v["agent_name"], response_engine=SimpleNamespace(**motor))
    def create_version(self, agent_id, **kw):
        self._validar("create_version", {"agent_id": agent_id, **kw})
        base = self._version(agent_id, kw["base_version"])
        numero = self._version(agent_id)["version"] + 1
        nueva = {**base, "version": numero, "publicada": False,
                 "response_engine": dict(base["response_engine"])}
        if nueva["response_engine"]["type"] == "retell-llm":
            llm = self.retell.llms[nueva["response_engine"]["llm_id"]]
            llm[numero] = {**llm[base["response_engine"]["version"]], "publicada": False}
            nueva["response_engine"]["version"] = numero
        self.retell.agentes[agent_id].append(nueva)
        self.retell.registro.append(("agent.create_version", agent_id, numero))
        return SimpleNamespace(version=numero)
    def update(self, agent_id, **kw):
        self._validar("update", {"agent_id": agent_id, **kw})
        v = self._version(agent_id, kw.pop("version", None))
        if v["publicada"]:
            raise ErrorRetell("Error code: 400 - una version publicada no se puede editar")
        motor = kw.get("response_engine")
        if motor and motor.get("version") != v["version"]:
            raise ErrorRetell(ERROR_VERSION)
        v.update(kw)
        self.retell.registro.append(("agent.update", agent_id, v["version"]))
    def create(self, **kw):
        self._validar("create", kw)
        if kw["response_engine"].get("version", 0) != 0:
            raise ErrorRetell(ERROR_VERSION)
        agent_id = f"agent_nuevo_{len(self.retell.agentes) + 1}"
        self.retell.agentes[agent_id] = [{**kw, "version": 0, "publicada": False,
                                          "response_engine": dict(kw["response_engine"])}]
        self.retell.registro.append(("agent.create", agent_id))
        return SimpleNamespace(agent_id=agent_id, version=0)
    def publish(self, agent_id, **kw):
        self._validar("publish", {"agent_id": agent_id, **kw})
        v = self._version(agent_id, kw["version"])
        if v["publicada"]:
            raise ErrorRetell("Error code: 400 - esa version ya esta publicada")
        v["publicada"] = True
        motor = v["response_engine"]
        if motor["type"] == "retell-llm":
            self.retell.llms[motor["llm_id"]][motor["version"]]["publicada"] = True
        self.retell.registro.append(("agent.publish", agent_id, kw["version"]))

class RetellSimulado:
    def __init__(self):
        self.llms, self.agentes, self.registro = {}, {}, []
        self.llm = LlmSimulado(self, {m: _parametros(getattr(LlmResource, m)) for m in ("create", "update")})
        self.agent = AgenteSimulado(self, {m: _parametros(getattr(AgentResource, m))
                                           for m in ("list", "retrieve", "create_version", "update",
                                                     "create", "publish")})
    def agregar(self, agent_id, nombre, publicada=True, motor="retell-llm", version=2):
        """Agente existente con su LLM en la misma version, como los deja Retell."""
        llm_id = f"llm_de_{agent_id}"
        if motor == "retell-llm":
            self.llms[llm_id] = {version: {"general_prompt": "prompt viejo", "general_tools": [],
                                           "publicada": publicada}}
        self.agentes[agent_id] = [{"agent_name": nombre, "version": version, "publicada": publicada,
                                   "response_engine": {"type": motor, "llm_id": llm_id, "version": version}}]
    def publicada(self, agent_id):
        return [v for v in self.agentes[agent_id] if v["publicada"]][-1]
    def nuevos_llm(self, desde):
        return [x for x in self.registro[desde:] if x[0] == "llm.create"]

retell = RetellSimulado()
servicio_asistente.cliente_retell = lambda: retell

runner = app.test_cli_runner()
def sincronizar(*opciones, **config):
    base = {"RETELL_API_KEY": "clave", "URL_PUBLICA": URL, "RETELL_VOZ_ID": "voz-es",
            **{v: "" for v in VARIABLES}}
    app.config.update({**base, **config})
    return runner.invoke(args=["sincronizar-asistentes", *opciones])

def llm_de(agent_id):
    """Configuracion del LLM en la version publicada del agente."""
    motor = retell.publicada(agent_id)["response_engine"]
    return retell.llms[motor["llm_id"]][motor["version"]]

from retell.types.llm_create_params import GeneralToolCustomTool
CAMPOS_HERRAMIENTA = set(GeneralToolCustomTool.__annotations__)

print("\n== 1. Configuracion de cada agente ==")
ESPERADAS = {"conductor": 5, "gestor": 14, "admin": 20, "cliente": 8}
with app.app_context():
    for definicion in conf.AGENTES:
        tools = conf.herramientas(definicion, URL)
        nombres = {t["name"] for t in tools}
        check(len(tools) == ESPERADAS[definicion.clave] and nombres == {f.nombre for f in funciones_de_rol(definicion.rol)},
              f"{definicion.clave}: {len(tools)} herramientas, las mismas del registro de funciones")
        check(all(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", n) for n in nombres),
              f"{definicion.clave}: nombres validos para Retell (letras, numeros, guion y guion bajo)")
        check(all(t["method"] == "POST" and t["args_at_root"] is False and t["url"].startswith(URL + "/api/asistente/")
                  for t in tools), f"{definicion.clave}: POST, sin 'args only' y con la URL publica")
        check(all(set(t["parameters"].get("required", [])) <= set(t["parameters"]["properties"]) for t in tools),
              f"{definicion.clave}: los parametros requeridos estan declarados")
        check(all(t["speak_during_execution"] is True and t["execution_message_type"] == "static_text"
                  and t["execution_message_description"] == "Un momento, lo reviso." for t in tools),
              f"{definicion.clave}: Talk While Waiting con 'Un momento, lo reviso.' en todas las funciones")
        check(all(set(t) <= CAMPOS_HERRAMIENTA for t in tools),
              f"{definicion.clave}: solo usa campos que el SDK define para una herramienta custom")
        acciones = [t for t in tools if FUNCIONES[(t["url"].split("/")[-2], t["name"])].accion]
        check(acciones and all("confirmar" in t["parameters"]["properties"]
                                and "confirmar" not in t["parameters"].get("required", []) for t in acciones),
              f"{definicion.clave}: sus {len(acciones)} acciones aceptan 'confirmar', opcional")
        texto = conf.prompt(definicion)
        check("{{nombre_usuario}}" in conf.bienvenida(definicion), f"{definicion.clave}: bienvenida con {{{{nombre_usuario}}}}")
        check("¿confirmas?" in texto and '"confirmar": true' in texto and "## Lo que no puedes hacer por voz" in texto
              and "{{fecha_hoy}}" in texto, f"{definicion.clave}: el prompt explica la confirmacion y lo que no puede hacer")

admin = next(d for d in conf.AGENTES if d.clave == "admin")
gestor = next(d for d in conf.AGENTES if d.clave == "gestor")
cliente = next(d for d in conf.AGENTES if d.clave == "cliente")
check("Usuarios" in conf.prompt(admin) and "contraseñas" in conf.prompt(admin),
      "el admin explica que usuarios, roles y contraseñas se manejan en pantalla")
check("Importar pedidos desde un archivo CSV" in conf.prompt(gestor) and "solo lo hace un administrador" in conf.prompt(gestor),
      "el gestor explica que importar CSV y ajustar inventario no van por voz")
check("Crear pedidos: todavía no está disponible" in conf.prompt(cliente) and "otras empresas" in conf.prompt(cliente),
      "el cliente explica que no crea pedidos ni ve datos de otros")
check({d.variable for d in conf.AGENTES} == set(VARIABLES), "un agente por variable de entorno")
with app.app_context():
    documento = conf.generar_documento()
check((RAIZ / "docs" / "configuracion_retell.md").read_text(encoding="utf-8") == documento,
      "docs/configuracion_retell.md esta al dia (flask --app run documentar-asistentes)")


print("\n== 2. Requisitos del comando ==")
r = sincronizar(RETELL_API_KEY="")
check(r.exit_code != 0 and "RETELL_API_KEY" in r.output and not retell.registro, "sin RETELL_API_KEY no llama a Retell")
r = sincronizar(URL_PUBLICA="")
check(r.exit_code != 0 and "URL_PUBLICA" in r.output and not retell.registro, "sin URL_PUBLICA tampoco")
r = sincronizar(URL_PUBLICA="http://localhost:5001")
check(r.exit_code != 0 and "https://" in r.output and not retell.registro, "una URL que no es https se rechaza")


print("\n== 3. Primera sincronizacion ==")
retell.agregar("agent_conductor", "Mi agente de ruta", publicada=True, version=2)
r = sincronizar(RETELL_AGENTE_CONDUCTOR_ID="agent_conductor", URL_PUBLICA=URL + "/")
check(r.exit_code == 0, f"termina sin errores ({r.exit_code}) {r.output[-300:] if r.exit_code else ''}")
check(not [x for x in retell.registro if x == ("agent.create", "agent_conductor")]
      and ("agent.create_version", "agent_conductor", 3) in retell.registro
      and ("llm.update", "llm_de_agent_conductor", 3) in retell.registro
      and ("agent.publish", "agent_conductor", 3) in retell.registro,
      "el conductor existente: borrador v3, su LLM en v3 actualizado, y se publica")
v_cond = retell.publicada("agent_conductor")
check(v_cond["language"] == "es-419" and v_cond["max_call_duration_ms"] == 300000
      and v_cond["end_call_after_silence_ms"] == 20000 and v_cond["agent_name"] == "SGDS - Conductor"
      and v_cond["response_engine"] == {"type": "retell-llm", "llm_id": "llm_de_agent_conductor", "version": 3},
      "con espanol latino, 5 minutos, 20 s de silencio y el mismo LLM en su misma version")
check(retell.agentes["agent_conductor"][0]["publicada"]
      and retell.llms["llm_de_agent_conductor"][2]["general_prompt"] == "prompt viejo",
      "la version publicada anterior queda intacta, con su version del LLM")
llm_cond = llm_de("agent_conductor")
check(len(llm_cond["general_tools"]) == 5 and all(t["url"].startswith(URL + "/api/asistente/conductor/") for t in llm_cond["general_tools"]),
      "su LLM trae las 5 funciones con la URL publica (sin la barra final)")
check(llm_cond["begin_message"] == conf.bienvenida(conf.AGENTES[0]) and llm_cond["general_prompt"] == conf.prompt(conf.AGENTES[0])
      and llm_cond["default_dynamic_variables"] == {"nombre_usuario": "", "fecha_hoy": ""},
      "con el prompt, la bienvenida y las variables dinamicas del repositorio")

creados = {n[1] for n in retell.registro if n[0] == "agent.create"}
check(len(creados) == 3 and len(retell.nuevos_llm(0)) == 3,
      f"crea los agentes de gestor, admin y cliente, cada uno con su LLM ({len(creados)})")
por_nombre = {retell.agentes[a][-1]["agent_name"]: a for a in creados}
check(set(por_nombre) == {"SGDS - Gestor logistico", "SGDS - Administrador", "SGDS - Cliente"},
      "con su nombre de agente")
check(all(retell.agentes[a][-1]["voice_id"] == "voz-es" and retell.agentes[a][-1]["publicada"]
          and retell.agentes[a][-1]["response_engine"]["version"] == 0 for a in creados),
      "con la voz de RETELL_VOZ_ID, el LLM en la version 0 como el agente, y publicados")
check([len(llm_de(por_nombre[n])["general_tools"]) for n in ("SGDS - Gestor logistico", "SGDS - Administrador", "SGDS - Cliente")]
      == [14, 20, 8], "gestor con 14 funciones, admin con 20 (las del gestor y las suyas) y cliente con 8")
for nombre, variable in (("SGDS - Gestor logistico", "RETELL_AGENTE_GESTOR_ID"),
                         ("SGDS - Administrador", "RETELL_AGENTE_ADMIN_ID"), ("SGDS - Cliente", "RETELL_AGENTE_CLIENTE_ID")):
    check(f"{variable}={por_nombre[nombre]}" in r.output, f"imprime {variable} para el .env")
check("RETELL_AGENTE_CONDUCTOR_ID=" not in r.output, "y no pide cambiar la del conductor, que ya estaba")


print("\n== 4. Segunda sincronizacion sobre agentes ya publicados (otra URL de ngrok) ==")
# Es el caso que fallo contra Retell real: apuntar el borrador v1 a un LLM
# nuevo en la version 0 da el 400 "Response engine version must match".
cliente_id = por_nombre["SGDS - Cliente"]
llm_cliente = retell.publicada(cliente_id)["response_engine"]["llm_id"]
retell.agent.create_version(cliente_id, base_version=0)
try:
    retell.agent.update(cliente_id, response_engine={"type": "retell-llm", "llm_id": "llm_otro", "version": 0})
    rechazo = None
except ErrorRetell as error:
    rechazo = str(error)
check(rechazo == ERROR_VERSION, "el simulador rechaza como Retell un LLM en otra version (400)")
retell.agentes[cliente_id].pop()          # descarta ese borrador de demostracion
del retell.llms[llm_cliente][1]

URL2 = "https://otra-9999.ngrok-free.app"
inicio = len(retell.registro)
ids = {"RETELL_AGENTE_CONDUCTOR_ID": "agent_conductor", "RETELL_AGENTE_GESTOR_ID": por_nombre["SGDS - Gestor logistico"],
       "RETELL_AGENTE_ADMIN_ID": por_nombre["SGDS - Administrador"], "RETELL_AGENTE_CLIENTE_ID": cliente_id}
r = sincronizar(URL_PUBLICA=URL2, **ids)
check(r.exit_code == 0 and "ERROR" not in r.output, f"termina sin errores: {r.output.strip()[-200:]}")
check(not [x for x in retell.registro[inicio:] if x[0] == "agent.create"] and not retell.nuevos_llm(inicio),
      "no crea agentes ni LLM nuevos: ningun LLM queda huerfano")
for variable, agent_id in ids.items():
    if variable == "RETELL_AGENTE_CONDUCTOR_ID":
        continue
    publicada = retell.publicada(agent_id)
    check(publicada["version"] == 1 and publicada["response_engine"]["version"] == 1
          and publicada["response_engine"]["llm_id"] == retell.agentes[agent_id][0]["response_engine"]["llm_id"],
          f"{variable}: queda publicada la v1, con su mismo LLM en la v1")
    check(retell.agentes[agent_id][0]["publicada"] and
          all(t["url"].startswith(URL + "/") for t in retell.llms[publicada["response_engine"]["llm_id"]][0]["general_tools"]),
          f"{variable}: la v0 y su LLM v0 quedan como estaban")
check(all(all(t["url"].startswith(URL2) for t in llm_de(a)["general_tools"]) for a in ids.values()),
      "y todos quedan publicados apuntando a la URL nueva")
check("pedidos-recientes" in {t["name"] for t in llm_de(cliente_id)["general_tools"]},
      "el cliente publicado tiene pedidos-recientes")
check("Agregue al .env" not in r.output, "sin pedir cambios en el .env")

inicio = len(retell.registro)
r = sincronizar("--rol", "cliente", URL_PUBLICA="https://tercera.ngrok-free.app", **ids)
tocados = {x[1] for x in retell.registro[inicio:] if x[0].startswith("agent.")}
check(r.exit_code == 0 and tocados == {cliente_id} and "cliente: actualizado" in r.output
      and "gestor:" not in r.output, "--rol cliente sincroniza solo el agente del cliente")
check(retell.publicada(cliente_id)["version"] == 2 and retell.publicada(ids["RETELL_AGENTE_GESTOR_ID"])["version"] == 1,
      "el cliente pasa a su v2 y los demas no cambian")

r = sincronizar(URL_PUBLICA=URL2, RETELL_AGENTE_CONDUCTOR_ID="agent_conductor")
check(r.exit_code == 0 and not [x for x in retell.registro if x[0] == "agent.create" and x[1] not in creados]
      and "encontrado por nombre" in r.output and f"RETELL_AGENTE_GESTOR_ID={ids['RETELL_AGENTE_GESTOR_ID']}" in r.output,
      "sin las variables, encuentra los agentes por su nombre en vez de duplicarlos")


print("\n== 5. Borrador pendiente y casos de error ==")
# El estado real que dejo la corrida fallida: v0 publicada y v1 en borrador,
# con su LLM en v1.
retell.agregar("agent_pendiente", "Pendiente", publicada=True, version=0)
retell.agent.create_version("agent_pendiente", base_version=0)
inicio = len(retell.registro)
r = sincronizar(URL_PUBLICA=URL2, **{**ids, "RETELL_AGENTE_CLIENTE_ID": "agent_pendiente"})
nuevos = retell.registro[inicio:]
check(r.exit_code == 0 and not [x for x in nuevos if x[:2] == ("agent.create_version", "agent_pendiente")]
      and ("llm.update", "llm_de_agent_pendiente", 1) in nuevos and ("agent.publish", "agent_pendiente", 1) in nuevos,
      "un borrador pendiente se reutiliza: actualiza su LLM v1 y lo publica, sin crear otra version")

retell.agregar("agent_flujo", "Flujo", motor="conversation-flow")
inicio = len(retell.registro)
r = sincronizar(URL_PUBLICA=URL2, **{**ids, "RETELL_AGENTE_CONDUCTOR_ID": "agent_flujo"})
check(r.exit_code == 1 and "conductor: ERROR" in r.output and "conversation-flow" in r.output
      and "docs/configuracion_retell.md" in r.output, "un agente con conversation flow se reporta para configurarlo a mano")
check(not retell.nuevos_llm(inicio) and "gestor: actualizado" in r.output,
      "sin crearle un LLM, y los demas agentes se sincronizan igual")

retell.agentes.clear()
r = sincronizar(RETELL_VOZ_ID="")
check(r.exit_code == 1 and r.output.count("RETELL_VOZ_ID") == 4 and not [x for x in retell.registro[-3:] if x[0] == "agent.create"],
      "sin RETELL_VOZ_ID no se crean agentes nuevos y se explica por que")


print("\n" + "=" * 55)
print("RESULTADO: " + ("TODAS LAS PRUEBAS PASARON" if not fallos else f"{len(fallos)} FALLAS"))
for f in fallos: print("   - " + f)
sys.exit(1 if fallos else 0)
