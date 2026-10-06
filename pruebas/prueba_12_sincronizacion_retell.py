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
        self.retell.llms[llm_id] = kw
        self.retell.registro.append(("llm.create", llm_id))
        return SimpleNamespace(llm_id=llm_id, version=0)

class AgenteSimulado(Recurso):
    def _ultima(self, agent_id):
        return self.retell.agentes[agent_id][-1]
    def list(self, **kw):
        self._validar("list", kw)
        return SimpleNamespace(
            items=[SimpleNamespace(agent_id=a, agent_name=v[-1]["agent_name"], channel="voice")
                   for a, v in self.retell.agentes.items()],
            has_more=False, pagination_key=None)
    def retrieve(self, agent_id, **kw):
        self._validar("retrieve", {"agent_id": agent_id, **kw})
        v = self._ultima(agent_id)
        return SimpleNamespace(agent_id=agent_id, version=v["version"], is_published=v["publicada"],
                               agent_name=v["agent_name"],
                               response_engine=SimpleNamespace(**v["response_engine"]))
    def create_version(self, agent_id, **kw):
        self._validar("create_version", {"agent_id": agent_id, **kw})
        base = next(v for v in self.retell.agentes[agent_id] if v["version"] == kw["base_version"])
        nueva = {**base, "version": self._ultima(agent_id)["version"] + 1, "publicada": False}
        self.retell.agentes[agent_id].append(nueva)
        self.retell.registro.append(("agent.create_version", agent_id, nueva["version"]))
        return SimpleNamespace(version=nueva["version"])
    def update(self, agent_id, **kw):
        self._validar("update", {"agent_id": agent_id, **kw})
        v = self._ultima(agent_id)
        if v["publicada"]:
            raise RuntimeError("Retell: una version publicada no se puede editar")
        v.update(kw)
        self.retell.registro.append(("agent.update", agent_id, v["version"]))
    def create(self, **kw):
        self._validar("create", kw)
        agent_id = f"agent_nuevo_{len(self.retell.agentes) + 1}"
        self.retell.agentes[agent_id] = [{**kw, "version": 0, "publicada": False}]
        self.retell.registro.append(("agent.create", agent_id))
        return SimpleNamespace(agent_id=agent_id, version=0)
    def publish(self, agent_id, **kw):
        self._validar("publish", {"agent_id": agent_id, **kw})
        v = next(x for x in self.retell.agentes[agent_id] if x["version"] == kw["version"])
        if v["publicada"]:
            raise RuntimeError("Retell: esa version ya esta publicada")
        v["publicada"] = True
        self.retell.registro.append(("agent.publish", agent_id, kw["version"]))

class RetellSimulado:
    def __init__(self):
        self.llms, self.agentes, self.registro = {}, {}, []
        self.llm = LlmSimulado(self, {"create": _parametros(LlmResource.create)})
        self.agent = AgenteSimulado(self, {m: _parametros(getattr(AgentResource, m))
                                           for m in ("list", "retrieve", "create_version", "update",
                                                     "create", "publish")})
    def agregar(self, agent_id, nombre, publicada=True, motor="retell-llm", version=2):
        self.agentes[agent_id] = [{"agent_name": nombre, "version": version, "publicada": publicada,
                                   "response_engine": {"type": motor, "llm_id": "llm_viejo"}}]
    def publicada(self, agent_id):
        return [v for v in self.agentes[agent_id] if v["publicada"]][-1]

retell = RetellSimulado()
servicio_asistente.cliente_retell = lambda: retell

runner = app.test_cli_runner()
def sincronizar(**config):
    base = {"RETELL_API_KEY": "clave", "URL_PUBLICA": URL, "RETELL_VOZ_ID": "voz-es",
            **{v: "" for v in VARIABLES}}
    app.config.update({**base, **config})
    return runner.invoke(args=["sincronizar-asistentes"])

def llm_de(agent_id):
    return retell.llms[retell.publicada(agent_id)["response_engine"]["llm_id"]]


print("\n== 1. Configuracion de cada agente ==")
ESPERADAS = {"conductor": 5, "gestor": 14, "admin": 20, "cliente": 7}
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
      and ("agent.publish", "agent_conductor", 3) in retell.registro,
      "el conductor existente se actualiza: borrador nuevo (v3) a partir del publicado y se publica")
v_cond = retell.publicada("agent_conductor")
check(v_cond["language"] == "es-419" and v_cond["max_call_duration_ms"] == 300000
      and v_cond["end_call_after_silence_ms"] == 20000 and v_cond["agent_name"] == "SGDS - Conductor",
      "con espanol latino, 5 minutos maximos y fin tras 20 s de silencio")
check(retell.agentes["agent_conductor"][0]["publicada"] and retell.agentes["agent_conductor"][0]["response_engine"]["llm_id"] == "llm_viejo",
      "la version publicada anterior queda intacta, con su LLM")
llm_cond = llm_de("agent_conductor")
check(len(llm_cond["general_tools"]) == 5 and all(t["url"].startswith(URL + "/api/asistente/conductor/") for t in llm_cond["general_tools"]),
      "su LLM nuevo trae las 5 funciones con la URL publica (sin la barra final)")
check(llm_cond["begin_message"] == conf.bienvenida(conf.AGENTES[0]) and llm_cond["general_prompt"] == conf.prompt(conf.AGENTES[0])
      and llm_cond["default_dynamic_variables"] == {"nombre_usuario": "", "fecha_hoy": ""},
      "con el prompt, la bienvenida y las variables dinamicas del repositorio")

creados = {n[1] for n in retell.registro if n[0] == "agent.create"}
check(len(creados) == 3, f"crea los agentes de gestor, admin y cliente ({len(creados)})")
por_nombre = {retell.agentes[a][-1]["agent_name"]: a for a in creados}
check(set(por_nombre) == {"SGDS - Gestor logistico", "SGDS - Administrador", "SGDS - Cliente"},
      "con su nombre de agente")
check(all(retell.agentes[a][-1]["voice_id"] == "voz-es" and retell.agentes[a][-1]["publicada"] for a in creados),
      "con la voz de RETELL_VOZ_ID, y publicados")
check([len(llm_de(por_nombre[n])["general_tools"]) for n in ("SGDS - Gestor logistico", "SGDS - Administrador", "SGDS - Cliente")]
      == [14, 20, 7], "gestor con 14 funciones, admin con 20 (las del gestor y las suyas) y cliente con 7")
for nombre, variable in (("SGDS - Gestor logistico", "RETELL_AGENTE_GESTOR_ID"),
                         ("SGDS - Administrador", "RETELL_AGENTE_ADMIN_ID"), ("SGDS - Cliente", "RETELL_AGENTE_CLIENTE_ID")):
    check(f"{variable}={por_nombre[nombre]}" in r.output, f"imprime {variable} para el .env")
check("RETELL_AGENTE_CONDUCTOR_ID=" not in r.output, "y no pide cambiar la del conductor, que ya estaba")


print("\n== 4. Volver a correrlo con otra URL de ngrok ==")
URL2 = "https://otra-9999.ngrok-free.app"
creaciones_antes = sum(1 for x in retell.registro if x[0] == "agent.create")
ids = {"RETELL_AGENTE_CONDUCTOR_ID": "agent_conductor", "RETELL_AGENTE_GESTOR_ID": por_nombre["SGDS - Gestor logistico"],
       "RETELL_AGENTE_ADMIN_ID": por_nombre["SGDS - Administrador"], "RETELL_AGENTE_CLIENTE_ID": por_nombre["SGDS - Cliente"]}
r = sincronizar(URL_PUBLICA=URL2, **ids)
check(r.exit_code == 0 and sum(1 for x in retell.registro if x[0] == "agent.create") == creaciones_antes,
      "con los agent_id en el .env no crea agentes nuevos")
check(all(all(t["url"].startswith(URL2) for t in llm_de(a)["general_tools"]) for a in ids.values()),
      "y todos quedan publicados apuntando a la URL nueva")
check("Agregue al .env" not in r.output, "sin pedir cambios en el .env")

r = sincronizar(URL_PUBLICA=URL2, RETELL_AGENTE_CONDUCTOR_ID="agent_conductor")
check(r.exit_code == 0 and sum(1 for x in retell.registro if x[0] == "agent.create") == creaciones_antes
      and "encontrado por nombre" in r.output and f"RETELL_AGENTE_GESTOR_ID={ids['RETELL_AGENTE_GESTOR_ID']}" in r.output,
      "sin las variables, encuentra los agentes por su nombre en vez de duplicarlos")


print("\n== 5. Casos de error ==")
retell.agregar("agent_borrador", "Borrador", publicada=False, version=5)
antes = len(retell.registro)
r = sincronizar(URL_PUBLICA=URL2, **{**ids, "RETELL_AGENTE_CONDUCTOR_ID": "agent_borrador"})
nuevos = retell.registro[antes:]
check(not [x for x in nuevos if x[:2] == ("agent.create_version", "agent_borrador")]
      and ("agent.publish", "agent_borrador", 5) in nuevos,
      "si la ultima version es un borrador, la actualiza y la publica sin crear otra")

retell.agregar("agent_flujo", "Flujo", motor="conversation-flow")
llms_antes = len(retell.llms)
r = sincronizar(URL_PUBLICA=URL2, **{**ids, "RETELL_AGENTE_CONDUCTOR_ID": "agent_flujo"})
check(r.exit_code == 1 and "conductor: ERROR" in r.output and "conversation-flow" in r.output
      and "docs/configuracion_retell.md" in r.output, "un agente con conversation flow se reporta para configurarlo a mano")
check(len(retell.llms) == llms_antes + 3 and "gestor: actualizado" in r.output,
      "sin crearle un LLM, y los demas agentes se sincronizan igual")

retell.agentes.clear()
r = sincronizar(RETELL_VOZ_ID="")
check(r.exit_code == 1 and r.output.count("RETELL_VOZ_ID") == 4 and not [x for x in retell.registro[-3:] if x[0] == "agent.create"],
      "sin RETELL_VOZ_ID no se crean agentes nuevos y se explica por que")


print("\n" + "=" * 55)
print("RESULTADO: " + ("TODAS LAS PRUEBAS PASARON" if not fallos else f"{len(fallos)} FALLAS"))
for f in fallos: print("   - " + f)
sys.exit(1 if fallos else 0)
