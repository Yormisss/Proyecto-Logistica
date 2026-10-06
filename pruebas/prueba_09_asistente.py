"""Asistente de voz del conductor (Retell AI) y avisos por Make.

Corre sin internet: el cliente de Retell y el POST a Make se simulan. Las
firmas X-Retell-Signature se generan con el propio SDK de Retell, de modo que
la verificacion que se prueba es la real.
"""

import json
import pathlib
import re
import sys
import time as reloj
from datetime import time, timedelta
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from retell.lib.webhook_auth import symmetric

from run import app
from app.extensions import db
from app.models import (Cliente, EstadoPedido, EstadoRuta, EventoPedido, MovimientoInventario,
                        Pedido, PedidoItem, Producto, PruebaEntrega, Rol, Ruta, SesionAsistente,
                        Usuario)
from app.services import asistente as servicio_asistente
from app.services import notificaciones
from app.tiempo import ahora, hoy

fallos = []
def check(c, m):
    print(("  OK   " if c else "  FALLA") + f" {m}")
    if not c: fallos.append(m)

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _preparar import reiniciar_base
reiniciar_base()

CLAVE = "clave-retell-prueba"
API = "/api/asistente/conductor"
NOTA = "Registrado por el asistente de voz"

app.config["WTF_CSRF_ENABLED"] = True
# La configuracion se fija aqui para que el .env local no cambie el resultado.
app.config.update(RETELL_API_KEY="", RETELL_AGENTE_CONDUCTOR_ID="", RETELL_AGENTE_GESTOR_ID="",
                  RETELL_AGENTE_ADMIN_ID="", RETELL_AGENTE_CLIENTE_ID="", MAKE_WEBHOOK_URL="",
                  MAKE_WEBHOOK_KEY="", CORREO_OPERACIONES="")


# ---- Retell simulado ----
class ServidorIce:
    def model_dump(self, exclude_none=False):
        return {"urls": "turn:turn.retell.invalid:3478", "username": "u", "credential": "c"}

class RetellSimulado:
    def __init__(self):
        self.llamadas, self.falla = [], False
        self.call = self
    def create_web_call(self, **kwargs):
        if self.falla:
            raise RuntimeError("Retell no disponible")
        self.llamadas.append(kwargs)
        return SimpleNamespace(access_token=f"token_{len(self.llamadas)}",
                               call_id=f"call_prueba_{len(self.llamadas)}",
                               transport="gateway", ice_servers=[ServidorIce()],
                               expires_at=0)

retell = RetellSimulado()
servicio_asistente.cliente_retell = lambda: retell


# ---- Make simulado ----
class RespuestaMake:
    def raise_for_status(self): pass

envios_make, modo_make = [], {"valor": "ok"}
def post_make(url, json=None, timeout=None, headers=None):
    envios_make.append({"url": url, "json": json, "timeout": timeout, "headers": headers})
    if modo_make["valor"] == "falla":
        raise ConnectionError("Make no responde")
    if modo_make["valor"] == "lento":
        reloj.sleep(2)
    return RespuestaMake()
notificaciones.requests.post = post_make


# ---- Utilidades ----
def sesion(correo, clave):
    c = app.test_client()
    html = c.get("/auth/login").data.decode()
    tok = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html).group(1)
    c.post("/auth/login", data={"csrf_token": tok, "correo": correo, "contrasena": clave})
    return c

def token_csrf(c, url):
    m = re.search(r'data-csrf="([^"]+)"', c.get(url).data.decode()) or \
        re.search(r'name="csrf_token"[^>]*value="([^"]+)"', c.get(url).data.decode())
    return m.group(1) if m else None

def pedir_llamada(c, url_pagina):
    return c.post("/asistente/llamada", headers={"X-CSRFToken": token_csrf(c, url_pagina) or ""})

retell_cliente = app.test_client()
def funcion(nombre, call_id, args=None, clave=CLAVE, firma=None, cuerpo=None, marca=None):
    cuerpo = cuerpo or json.dumps({"call": {"call_id": call_id}, "name": nombre, "args": args or {}},
                                  ensure_ascii=False)
    cabeceras = {"Content-Type": "application/json"}
    if firma is None:
        firma = symmetric["sign"](cuerpo, clave, marca)
    if firma:
        cabeceras["X-Retell-Signature"] = firma
    r = retell_cliente.post(f"{API}/{nombre}", data=cuerpo.encode(), headers=cabeceras)
    return r.status_code, (r.get_json(silent=True) or {})

def mensaje(respuesta):
    return respuesta[1].get("mensaje", "")


# ---- Escenario ----
with app.app_context():
    c1 = db.session.query(Usuario).filter_by(correo="conductor1@sgds.com").one()
    c2 = db.session.query(Usuario).filter_by(correo="conductor2@sgds.com").one()
    desp = db.session.query(Usuario).filter_by(correo="despachador@sgds.com").one()
    portal = db.session.query(Cliente).filter_by(nombre="Supermercado El Portal").one()
    esquina = db.session.query(Cliente).filter_by(nombre="Tienda La Esquina").one()
    producto = db.session.query(Producto).filter_by(sku="SKU-1001").one()
    c1_id, c2_id, desp_id, portal_id, esquina_id, prod_id = c1.id, c2.id, desp.id, portal.id, esquina.id, producto.id
    ruta1 = db.session.query(Ruta).filter_by(conductor_id=c1_id, fecha=hoy()).one()
    ruta1_codigo = ruta1.codigo
    # Paradas de conductor1 que deben quedar intactas cuando opera conductor2.
    estados_ruta1 = {p.id: p.estado for p in ruta1.pedidos}

print(f"\nEscenario: conductor1 con {ruta1_codigo}; conductor2 aun sin ruta para hoy.")

conductor1 = sesion("conductor1@sgds.com", "Conductor123*")
conductor2 = sesion("conductor2@sgds.com", "Conductor123*")
cliente = sesion("cliente@sgds.com", "Cliente123*")
despachador = sesion("despachador@sgds.com", "Despacho123*")


print("\n== 1. Sin configuracion de Retell la app sigue igual ==")
r = conductor1.get("/conductor/")
check(r.status_code == 200 and 'id="asistente"' not in r.data.decode(), "sin API key no se muestra el boton")
r = conductor1.post("/asistente/llamada", headers={"X-CSRFToken": token_csrf(conductor1, "/conductor/")})
check(r.status_code == 403 and not retell.llamadas, "sin API key no se crea ninguna llamada (403)")
s = funcion("mi-ruta", "call_cualquiera")
check(s[0] == 401, f"sin API key ninguna firma es valida ({s[0]})")

app.config.update(RETELL_API_KEY=CLAVE, RETELL_AGENTE_CONDUCTOR_ID="")
check('id="asistente"' not in conductor1.get("/conductor/").data.decode(),
      "con API key pero sin agente del rol, tampoco aparece")

app.config.update(RETELL_AGENTE_CONDUCTOR_ID="agent_conductor")
html = conductor1.get("/conductor/").data.decode()
check('id="asistente"' in html, "con API key y agente, el conductor ve el boton")
check(CLAVE not in html, "la pagina no contiene la API key")
check('id="asistente"' not in cliente.get("/portal/").data.decode(), "el cliente no ve el boton")
check('id="asistente"' not in despachador.get("/admin/").data.decode(), "el gestor logistico no ve el boton")


print("\n== 2. POST /asistente/llamada ==")
r = conductor1.post("/asistente/llamada")
check(r.status_code == 400 and not retell.llamadas, "sin token CSRF se rechaza (400) y no llama a Retell")

anonimo = app.test_client()
r = anonimo.post("/asistente/llamada", headers={"X-CSRFToken": token_csrf(anonimo, "/auth/login")})
check(r.status_code == 302 and "/auth/login" in r.headers.get("Location", ""),
      "sin sesion (con token CSRF valido) redirige al login")

# /auth/perfil tiene formulario (y token CSRF) para cualquier rol; asi el 403
# que se mide es el de autorizacion y no un 400 por falta de token.
r = pedir_llamada(cliente, "/auth/perfil")
check(r.status_code == 403, f"un cliente no puede iniciar llamada con el agente del conductor ({r.status_code})")
r = pedir_llamada(despachador, "/auth/perfil")
check(r.status_code == 403, "un rol sin agente recibe 403")
check(not retell.llamadas, "ninguno de los dos llego a crear una llamada en Retell")

r = pedir_llamada(conductor1, "/conductor/")
datos = r.get_json() or {}
check(r.status_code == 200, f"el conductor inicia la llamada ({r.status_code})")
check(set(datos) == {"access_token", "call_id", "transport", "ice_servers"},
      f"devuelve solo los datos para unirse a la llamada {sorted(datos)}")
check(datos.get("transport") == "gateway" and datos.get("ice_servers"), "incluye transporte gateway y servidores ICE")
check(CLAVE not in r.data.decode(), "la respuesta no contiene la API key")
check(retell.llamadas[-1] == {"agent_id": "agent_conductor",
                              "retell_llm_dynamic_variables": {"nombre_usuario": "Andres Molina",
                                                               "fecha_hoy": hoy().isoformat()}},
      "usa el agente del conductor y pasa su nombre y la fecha de hoy como variables dinamicas")
CALL_C1 = datos.get("call_id")
with app.app_context():
    s = db.session.query(SesionAsistente).filter_by(call_id=CALL_C1).one()
    check(s.usuario_id == c1_id and s.rol == Rol.CONDUCTOR, "guarda call_id -> conductor1 con su rol")
    check(s.vence_en - s.creada_en == timedelta(minutes=10), "la sesion vence a los 10 minutos")

retell.falla = True
total_sesiones = None
with app.app_context():
    total_sesiones = db.session.query(SesionAsistente).count()
r = pedir_llamada(conductor1, "/conductor/")
check(r.status_code == 502 and "asistente" in (r.get_json() or {}).get("error", ""),
      "si Retell falla responde 502 con un mensaje en espanol")
with app.app_context():
    check(db.session.query(SesionAsistente).count() == total_sesiones, "y no registra ninguna sesion")
retell.falla = False

CALL_C2 = pedir_llamada(conductor2, "/conductor/").get_json()["call_id"]


print("\n== 3. Firma X-Retell-Signature ==")
s = funcion("mi-ruta", CALL_C1, firma="")
check(s[0] == 401, f"sin cabecera de firma: 401 ({s[0]})")
s = funcion("mi-ruta", CALL_C1, firma="firma-cualquiera")
check(s[0] == 401, "con una firma mal formada: 401")
s = funcion("mi-ruta", CALL_C1, clave="otra-clave")
check(s[0] == 401, "firmada con otra clave: 401")
cuerpo = json.dumps({"call": {"call_id": CALL_C1}, "name": "mi-ruta", "args": {}})
firma_original = symmetric["sign"](cuerpo, CLAVE)
s = funcion("mi-ruta", None, cuerpo=cuerpo.replace("mi-ruta", "mi-rutA"), firma=firma_original)
check(s[0] == 401, "cuerpo alterado despues de firmar: 401")
s = funcion("mi-ruta", CALL_C1, marca=int((reloj.time() - 600) * 1000))
check(s[0] == 401, "firma de hace 10 minutos (reenvio): 401")
s = funcion("mi-ruta", CALL_C1)
check(s[0] == 200, f"firma valida: 200 ({s[0]})")


print("\n== 4. Identidad por call_id ==")
s = funcion("mi-ruta", "call_desconocido")
check(s[0] == 403, f"call_id desconocido: 403 ({s[0]})")
s = funcion("mi-ruta", None)
check(s[0] == 403, "sin call_id: 403")
with app.app_context():
    db.session.add(SesionAsistente(call_id="call_vencido", usuario_id=c1_id, rol=Rol.CONDUCTOR,
                                   creada_en=ahora() - timedelta(minutes=15),
                                   vence_en=ahora() - timedelta(minutes=5)))
    cliente_usuario = db.session.query(Usuario).filter_by(correo="cliente@sgds.com").one()
    db.session.add(SesionAsistente(call_id="call_de_cliente", usuario_id=cliente_usuario.id,
                                   rol=Rol.CLIENTE, vence_en=ahora() + timedelta(minutes=10)))
    db.session.commit()
s = funcion("mi-ruta", "call_vencido")
check(s[0] == 403, f"call_id vencido: 403 ({s[0]})")
s = funcion("marcar-en-camino", "call_vencido", {"orden": 1})
check(s[0] == 403, "un call_id vencido tampoco puede ejecutar acciones")
s = funcion("mi-ruta", "call_de_cliente")
check(s[0] == 403, "call_id abierto por un cliente: 403")

# Un conductor desactivado o reasignado a otro rol pierde la sesion de inmediato.
with app.app_context():
    db.session.get(Usuario, c2_id).activo = False; db.session.commit()
s = funcion("mi-ruta", CALL_C2)
check(s[0] == 403, "call_id de un conductor desactivado: 403")
with app.app_context():
    u = db.session.get(Usuario, c2_id); u.activo = True; u.rol = Rol.DESPACHADOR; db.session.commit()
s = funcion("mi-ruta", CALL_C2)
check(s[0] == 403, "call_id de un usuario que ya no es conductor: 403")
with app.app_context():
    db.session.get(Usuario, c2_id).rol = Rol.CONDUCTOR; db.session.commit()

s = funcion("mi-ruta", CALL_C2)
check(s[0] == 200 and "No tienes una ruta activa" in mensaje(s),
      "conductor2 sin ruta hoy no recibe la ruta de conductor1")
with app.app_context():
    finalizadas_previas = db.session.query(Ruta).filter(
        Ruta.conductor_id == c2_id, Ruta.estado == EstadoRuta.FINALIZADA, Ruta.fecha < hoy()).count()
html = conductor2.get("/conductor/").data.decode()
check(finalizadas_previas and "No tiene una ruta asignada para hoy" in html
      and "Completaste tu ruta de hoy" not in html,
      "las rutas finalizadas de dias anteriores no cuentan como la de hoy (vista)")
check("Completaste" not in mensaje(s), "ni para el asistente")
s = funcion("detalle-parada", CALL_C2, {"orden": 1})
check(ruta1_codigo not in mensaje(s) and "No tienes una ruta activa" in mensaje(s),
      "ni puede consultar paradas de conductor1 por su numero")

# Ruta de hoy para conductor2: una parada de El Portal (con correo) y una sin correo.
with app.app_context():
    db.session.get(Cliente, portal_id).correo = "compras@portal.invalid"
    ruta2 = Ruta(codigo="RUT-ASIS-02", fecha=hoy(), estado=EstadoRuta.PLANIFICADA,
                 conductor_id=c2_id, distancia_km=8, duracion_min=25)
    db.session.add(ruta2); db.session.flush()
    p_portal = Pedido(codigo="ASIS-001", cliente_id=portal_id, cliente_nombre="Supermercado El Portal",
                      direccion="Av. Cra 68 #75-50", ciudad="Bogota", fecha_despacho=hoy(),
                      estado=EstadoPedido.ASIGNADO, ruta_id=ruta2.id, orden_en_ruta=1,
                      cliente_telefono="3115550111", creado_por_id=desp_id,
                      ventana_inicio=time(8, 0), ventana_fin=time(11, 0))
    p_esquina = Pedido(codigo="ASIS-002", cliente_id=esquina_id, cliente_nombre="Tienda La Esquina",
                       direccion="Calle 63 #24-18", ciudad="Bogota", fecha_despacho=hoy(),
                       estado=EstadoPedido.ASIGNADO, ruta_id=ruta2.id, orden_en_ruta=2,
                       creado_por_id=desp_id)
    db.session.add_all([p_portal, p_esquina]); db.session.flush()
    db.session.add(PedidoItem(pedido_id=p_portal.id, producto_id=prod_id, cantidad=4))
    db.session.add(PedidoItem(pedido_id=p_esquina.id, producto_id=prod_id, cantidad=2))
    db.session.commit()
    pid_portal, pid_esquina = p_portal.id, p_esquina.id

s = funcion("mi-ruta", CALL_C2)
check("RUT-ASIS-02" in mensaje(s) and ruta1_codigo not in mensaje(s),
      "con call_id de conductor2 solo ve su propia ruta")
s = funcion("mi-ruta", CALL_C1)
check(ruta1_codigo in mensaje(s) and "RUT-ASIS-02" not in mensaje(s),
      "con call_id de conductor1 solo ve la suya")


print("\n== 5. Custom functions sobre la ruta de hoy ==")
s = funcion("mi-ruta", CALL_C2)
check(mensaje(s) == "Tu ruta de hoy es la RUT-ASIS-02, con 2 paradas: 0 entregadas, 0 fallidas y "
      "2 pendientes. La siguiente es la parada 1, Supermercado El Portal.",
      "mi-ruta resume la ruta en una frase")
s = funcion("siguiente-parada", CALL_C2)
check("parada 1: Supermercado El Portal" in mensaje(s) and "de 08:00 a 11:00" in mensaje(s),
      "siguiente-parada da cliente, direccion y ventana")
s = funcion("detalle-parada", CALL_C2, {"orden": 1})
check("4 unidades" in mensaje(s) and "3115550111" in mensaje(s), "detalle-parada incluye unidades y telefono")
s = funcion("detalle-parada", CALL_C2, {"orden": 9})
check(s[0] == 200 and "No encontré la parada 9" in mensaje(s), "una parada inexistente se responde en voz")
s = funcion("detalle-parada", CALL_C2, {})
check("Necesito el número" in mensaje(s), "sin orden pide el numero de parada")
check(all(len(mensaje(funcion(n, CALL_C2, {"orden": 1}))) < 300
          for n in ("mi-ruta", "siguiente-parada", "detalle-parada")),
      "las respuestas son breves (menos de 300 caracteres)")

r = retell_cliente.post(f"{API}/marcar-entregado", data=b"{}", headers={"Content-Type": "application/json"})
check(r.status_code == 404, "no existe una funcion para marcar ENTREGADO")

app.config["MAKE_WEBHOOK_URL"] = "https://hook.make.invalid/sgds"
with app.app_context():
    stock_inicial = db.session.get(Producto, prod_id).stock_actual

s = funcion("marcar-en-camino", CALL_C2, {"orden": 1})
notificaciones.esperar_envios()
check("quedó en camino" in mensaje(s), "marcar-en-camino confirma en voz")
with app.app_context():
    p = db.session.get(Pedido, pid_portal)
    ev = db.session.query(EventoPedido).filter_by(pedido_id=pid_portal).order_by(EventoPedido.id.desc()).first()
    check(p.estado == EstadoPedido.EN_RUTA, "el pedido pasa a EN_RUTA")
    check(ev.nota == NOTA and ev.usuario_id == c2_id, "el evento queda con la nota del asistente y el conductor")
    check(db.session.get(Ruta, p.ruta_id).estado == EstadoRuta.EN_CURSO, "la ruta pasa a EN_CURSO (regla de despacho.py)")

s = funcion("marcar-en-camino", CALL_C2, {"orden": 1})
check("No pude" in mensaje(s) and "En ruta" in mensaje(s), "una transicion invalida se explica en voz")

envios_antes = len(envios_make)
s = funcion("registrar-fallo", CALL_C2, {"orden": 2, "motivo": "  "})
check("Necesito el motivo" in mensaje(s), "registrar-fallo exige el motivo")
s = funcion("registrar-fallo", CALL_C2, {"orden": 2, "motivo": "Cliente ausente"})
check("Necesito" not in mensaje(s) and "No pude" in mensaje(s),
      "no se puede fallar una parada que no ha salido (ASIGNADO -> FALLIDO)")
funcion("marcar-en-camino", CALL_C2, {"orden": 2})
s = funcion("registrar-fallo", CALL_C2, {"orden": 2, "motivo": "Cliente ausente"})
notificaciones.esperar_envios()
check("como no entregada por: Cliente ausente" in mensaje(s), "registrar-fallo confirma en voz")
with app.app_context():
    p = db.session.get(Pedido, pid_esquina)
    prueba = db.session.query(PruebaEntrega).filter_by(pedido_id=pid_esquina).one()
    ev = db.session.query(EventoPedido).filter_by(pedido_id=pid_esquina).order_by(EventoPedido.id.desc()).first()
    check(p.estado == EstadoPedido.FALLIDO and prueba.motivo_fallo == "Cliente ausente",
          "el pedido queda FALLIDO con el motivo en la prueba de entrega")
    check(ev.nota == NOTA, "el fallo queda con la nota del asistente")
    check(db.session.get(Producto, prod_id).stock_actual == stock_inicial, "el fallo no toca el inventario")
check(len(envios_make) == envios_antes, "Tienda La Esquina no tiene correo: no se envio nada a Make")

with app.app_context():
    intactos = {p.id: p.estado for p in db.session.get(Ruta, db.session.query(Ruta).filter_by(
        codigo=ruta1_codigo).one().id).pedidos}
check(intactos == estados_ruta1, "las paradas de conductor1 no cambiaron")


print("\n== 6. Avisos por Make ==")
aviso = next((e for e in envios_make if e["json"]["codigo"] == "ASIS-001"), None)
check(aviso is not None, "marcar-en-camino de El Portal envio un aviso a Make")
if aviso:
    check(set(aviso["json"]) == {"tipo", "codigo", "estado", "cliente", "correo", "direccion", "ventana", "hora"},
          "el aviso lleva tipo, codigo, estado, cliente, correo, direccion, ventana y hora")
    check(aviso["json"]["tipo"] == "pedido_estado", "de tipo pedido_estado")
    check(aviso["json"]["estado"] == "EN_RUTA" and aviso["json"]["correo"] == "compras@portal.invalid"
          and aviso["json"]["ventana"] == "08:00 - 11:00", "con los datos del pedido")
    check(re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d", aviso["json"]["hora"]) is not None, "hora AAAA-MM-DD HH:MM")
    check(aviso["url"] == "https://hook.make.invalid/sgds" and aviso["timeout"] == (3, 5),
          "va a MAKE_WEBHOOK_URL con timeout corto")
check(len([e for e in envios_make if e["json"]["codigo"] == "ASIS-001"]) == 1,
      "la transicion invalida (rollback) no genero un segundo aviso")

# Make caido y lento: la entrega desde la vista movil se registra igual.
modo_make["valor"] = "falla"
envios_antes = len(envios_make)
t = token_csrf(conductor2, f"/conductor/parada/{pid_portal}")
r = conductor2.post(f"/conductor/parada/{pid_portal}/entregar",
                    data={"csrf_token": t, "receptor_nombre": "Ana Ruiz"}, follow_redirects=True)
notificaciones.esperar_envios()
check(r.status_code == 200 and b"Entrega registrada" in r.data, "con Make caido la entrega se confirma al conductor")
with app.app_context():
    p = db.session.get(Pedido, pid_portal)
    check(p.estado == EstadoPedido.ENTREGADO and p.inventario_descontado, "el pedido queda ENTREGADO")
    check(db.session.get(Producto, prod_id).stock_actual == stock_inicial - 4, "y el inventario se descuenta")
    check(db.session.query(MovimientoInventario).filter_by(pedido_id=pid_portal).count() == 1,
          "con su movimiento de inventario")
check(len(envios_make) == envios_antes + 1 and envios_make[-1]["json"]["estado"] == "ENTREGADO",
      "el aviso de ENTREGADO se intento enviar (y fallo sin consecuencias)")

modo_make["valor"] = "lento"
with app.app_context():
    p = db.session.get(Pedido, pid_esquina)
    db.session.get(Cliente, esquina_id).correo = "esquina@tienda.invalid"
    db.session.commit()
t = token_csrf(conductor2, f"/conductor/parada/{pid_esquina}")
inicio = reloj.perf_counter()
r = conductor2.post(f"/conductor/parada/{pid_esquina}/reintentar", data={"csrf_token": t})
demora = reloj.perf_counter() - inicio
check(r.status_code == 302 and demora < 1.5,
      f"con Make tardando 2 s el conductor no espera ({demora:.2f} s)")
notificaciones.esperar_envios()
check(envios_make[-1]["json"]["codigo"] == "ASIS-002", "el aviso salio en segundo plano")
modo_make["valor"] = "ok"


print("\n== 7. Ruta finalizada de hoy ==")
# RUT-ASIS-02 tiene ASIS-001 entregado y ASIS-002 en reintento. Se agrega una
# parada anulada y se registra el fallo de ASIS-002: todas quedan en estado
# final y la ruta pasa a FINALIZADA.
with app.app_context():
    ruta2 = db.session.query(Ruta).filter_by(codigo="RUT-ASIS-02").one()
    db.session.add(Pedido(codigo="ASIS-003", cliente_id=esquina_id, cliente_nombre="Tienda La Esquina",
                          direccion="Calle 63 #24-18", ciudad="Bogota", fecha_despacho=hoy(),
                          estado=EstadoPedido.CANCELADO, ruta_id=ruta2.id, orden_en_ruta=3,
                          creado_por_id=desp_id))
    db.session.commit()
funcion("registrar-fallo", CALL_C2, {"orden": 2, "motivo": "Establecimiento cerrado"})
notificaciones.esperar_envios()
with app.app_context():
    check(db.session.query(Ruta).filter_by(codigo="RUT-ASIS-02").one().estado == EstadoRuta.FINALIZADA,
          "con todas las paradas en estado final la ruta queda FINALIZADA")

html = conductor2.get("/conductor/").data.decode()
check("Completaste tu ruta de hoy" in html and "No tiene una ruta asignada" not in html,
      "la vista muestra 'Completaste tu ruta de hoy' en lugar de 'sin ruta asignada'")
check("RUT-ASIS-02" in html, "con el codigo de la ruta finalizada")
resumen = {etiqueta: int(numero) for numero, etiqueta in
           re.findall(r"<strong>(\d+)</strong><span>(Entregados|Fallidos|Cancelados)</span>", html)}
check(resumen == {"Entregados": 1, "Fallidos": 1, "Cancelados": 1},
      f"resume entregados, fallidos y cancelados {resumen}")
check(re.search(r'href="/conductor/historial"[^>]*>Ver historial de rutas<', html) is not None,
      "incluye un enlace al historial")
check("Iniciar ruta" not in html and "parada-enlace" not in html, "sin boton de inicio ni lista de paradas")

s = funcion("mi-ruta", CALL_C2)
check(mensaje(s) == "Completaste tu ruta de hoy, la RUT-ASIS-02, con 3 paradas: 1 entregada, "
      "1 fallida y 1 cancelada. Puedes ver el detalle en tu historial.",
      f"mi-ruta responde lo mismo en voz ({mensaje(s)})")
check(len(mensaje(s)) < 300, "la respuesta es breve")

html = conductor1.get("/conductor/").data.decode()
check("Completaste" not in html and ruta1_codigo in html, "conductor1, con ruta activa, sigue viendo sus paradas")
check(ruta1_codigo in mensaje(funcion("mi-ruta", CALL_C1)), "y el asistente le resume su ruta activa")

# Si el gestor le asigna otra ruta el mismo dia, la activa tiene prioridad.
with app.app_context():
    ruta3 = Ruta(codigo="RUT-ASIS-03", fecha=hoy(), estado=EstadoRuta.PLANIFICADA,
                 conductor_id=c2_id, distancia_km=3, duracion_min=10)
    db.session.add(ruta3); db.session.flush()
    db.session.add(Pedido(codigo="ASIS-004", cliente_id=portal_id, cliente_nombre="Supermercado El Portal",
                          direccion="Av. Cra 68 #75-50", ciudad="Bogota", fecha_despacho=hoy(),
                          estado=EstadoPedido.ASIGNADO, ruta_id=ruta3.id, orden_en_ruta=1,
                          creado_por_id=desp_id))
    db.session.commit()
html = conductor2.get("/conductor/").data.decode()
check("RUT-ASIS-03" in html and "Completaste" not in html, "una ruta nueva del mismo dia reemplaza el resumen")
s = funcion("mi-ruta", CALL_C2)
check(mensaje(s).startswith("Tu ruta de hoy es la RUT-ASIS-03"), "tambien en el asistente")


print("\n== 8. Migracion de la tabla ==")
from migraciones.m002_sesiones_asistente import aplicar
with app.app_context():
    check(aplicar(verboso=False) is False, "sobre una base que ya la tiene no crea nada")
    check(aplicar(verboso=False) is False, "volver a ejecutarla tampoco")


print("\n== 9. Demo de Make con la semilla ==")
# Se vuelve a sembrar con SEMILLA_CORREO_CLIENTE, como lo haria la demo, y se
# recorre el camino del conductor sin ningun paso manual.
import os, subprocess
from _preparar import PYTHON, RAIZ
entorno = {**os.environ, "SEMILLA_CORREO_CLIENTE": "demo@portal.invalid"}
subprocess.run([str(PYTHON), "-m", "flask", "--app", "run", "reset-db"], cwd=RAIZ,
               capture_output=True, env=entorno)
subprocess.run([str(PYTHON), "seed.py"], cwd=RAIZ, capture_output=True, env=entorno)
with app.app_context():
    db.session.remove()
    portal = db.session.query(Cliente).filter_by(nombre="Supermercado El Portal").one()
    check(portal.correo == "demo@portal.invalid", "seed.py asigna SEMILLA_CORREO_CLIENTE a El Portal")
    check(db.session.query(Cliente).filter(Cliente.correo.isnot(None)).count() == 1,
          "ningun otro cliente sembrado tiene correo")
    conductor_demo = db.session.query(Usuario).filter_by(correo="conductor1@sgds.com").one()
    ruta_demo = db.session.query(Ruta).filter_by(conductor_id=conductor_demo.id, fecha=hoy()).one()
    pedido_demo = next(p for p in ruta_demo.pedidos if p.cliente_id == portal.id)
    check(pedido_demo.estado == EstadoPedido.ASIGNADO,
          "el pedido de hoy de El Portal queda ASIGNADO en la ruta de conductor1")
    pid_demo, orden_demo = pedido_demo.id, pedido_demo.orden_en_ruta

conductor1 = sesion("conductor1@sgds.com", "Conductor123*")
call_demo = pedir_llamada(conductor1, "/conductor/").get_json()["call_id"]
envios_antes = len(envios_make)
funcion("marcar-en-camino", call_demo, {"orden": orden_demo})
t = token_csrf(conductor1, f"/conductor/parada/{pid_demo}")
conductor1.post(f"/conductor/parada/{pid_demo}/entregar", data={"csrf_token": t})
notificaciones.esperar_envios()
demo = [(e["json"]["estado"], e["json"]["correo"]) for e in envios_make[envios_antes:]]
check(demo == [("EN_RUTA", "demo@portal.invalid"), ("ENTREGADO", "demo@portal.invalid")],
      f"en camino por voz y entrega en pantalla generan los dos avisos {demo}")


print("\n" + "=" * 55)
print("RESULTADO: " + ("TODAS LAS PRUEBAS PASARON" if not fallos else f"{len(fallos)} FALLAS"))
for f in fallos: print("   - " + f)
sys.exit(1 if fallos else 0)
