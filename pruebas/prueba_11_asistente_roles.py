"""Asistente de voz por rol: permisos, confirmacion con estado y busqueda.

Corre sin internet: Retell y Make se simulan, y las firmas X-Retell-Signature
se generan con el propio SDK de Retell para probar la verificacion real.
"""

import json
import pathlib
import re
import sys
from datetime import timedelta
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from retell.lib.webhook_auth import symmetric
from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, select

from run import app
from app.controllers.asistente_api import (
    FUNCIONES, exigir_confirmacion, funcion_asistente, funciones_de_rol, responder,
)
from app.extensions import db
from app.models import (Cliente, EstadoPedido, EstadoRuta, Pedido, Producto, Rol, Ruta,
                        SesionAsistente, Usuario)
from app.services import asistente as servicio_asistente
from app.services import notificaciones
from app.services.busqueda_voz import (buscar_clientes, buscar_pedidos, buscar_productos,
                                       buscar_sedes, describir)
from app.tiempo import ahora, hoy

fallos = []
def check(c, m):
    print(("  OK   " if c else "  FALLA") + f" {m}")
    if not c: fallos.append(m)

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _preparar import reiniciar_base
reiniciar_base()

CLAVE = "clave-retell-prueba"
AGENTES = {"RETELL_AGENTE_CONDUCTOR_ID": "agent_conductor", "RETELL_AGENTE_GESTOR_ID": "agent_gestor",
           "RETELL_AGENTE_ADMIN_ID": "agent_admin", "RETELL_AGENTE_CLIENTE_ID": "agent_cliente"}

app.config["WTF_CSRF_ENABLED"] = True
# La configuracion se fija aqui para que el .env local no cambie el resultado.
app.config.update(RETELL_API_KEY=CLAVE, MAKE_WEBHOOK_URL="", MAKE_WEBHOOK_KEY="",
                  CORREO_OPERACIONES="", **{k: "" for k in AGENTES})


# ---- Retell y Make simulados ----
class RetellSimulado:
    def __init__(self):
        self.llamadas = []
        self.call = self
    def create_web_call(self, **kwargs):
        self.llamadas.append(kwargs)
        return SimpleNamespace(access_token="tok", call_id=f"call_roles_{len(self.llamadas)}",
                               transport="gateway", ice_servers=[], expires_at=0)

retell = RetellSimulado()
servicio_asistente.cliente_retell = lambda: retell

envios_make = []
notificaciones.requests.post = lambda url, json=None, timeout=None, headers=None: (
    envios_make.append(json), SimpleNamespace(raise_for_status=lambda: None))[1]


# ---- Utilidades ----
def sesion(correo, clave):
    c = app.test_client()
    html = c.get("/auth/login").data.decode()
    tok = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html).group(1)
    c.post("/auth/login", data={"csrf_token": tok, "correo": correo, "contrasena": clave})
    return c

def csrf_de(c, url):
    m = re.search(r'data-csrf="([^"]+)"', c.get(url).data.decode()) or \
        re.search(r'name="csrf_token"[^>]*value="([^"]+)"', c.get(url).data.decode())
    return m.group(1) if m else ""

retell_cliente = app.test_client()
def funcion(ruta, call_id, args=None):
    """POST firmado a /api/asistente/<ruta>, como lo haria Retell."""
    cuerpo = json.dumps({"call": {"call_id": call_id}, "name": ruta.split("/")[-1], "args": args or {}},
                        ensure_ascii=False)
    r = retell_cliente.post(f"/api/asistente/{ruta}", data=cuerpo.encode(),
                            headers={"Content-Type": "application/json",
                                     "X-Retell-Signature": symmetric["sign"](cuerpo, CLAVE)})
    return r.status_code, (r.get_json(silent=True) or {})

def mensaje(respuesta):
    return respuesta[1].get("mensaje", "")

TABLAS_SIN_DATOS = {"sesiones_asistente", "mediciones_rendimiento"}
def huella():
    """Todas las filas de todas las tablas de negocio, para comparar antes y despues."""
    with app.app_context():
        db.session.remove()
        return {t.name: sorted(map(repr, db.session.execute(select(t)).all()))
                for t in db.metadata.sorted_tables if t.name not in TABLAS_SIN_DATOS}


# ---- Funciones de prueba ----
# Una consulta del gestor y una accion repetible, registradas solo en este
# proceso, para probar el mecanismo con independencia de las funciones reales.
ejecuciones = []

@funcion_asistente("gestor", "eco-prueba", roles=(Rol.DESPACHADOR, Rol.ADMIN),
                   descripcion="Solo para pruebas.")
def eco_prueba():
    return responder("eco")

@funcion_asistente("gestor", "accion-prueba", roles=(Rol.DESPACHADOR, Rol.ADMIN), accion=True,
                   descripcion="Solo para pruebas.", parametros={"valor": {"type": "string"}})
def accion_prueba():
    from flask import g
    pendiente = exigir_confirmacion(f"Voy a registrar {g.argumentos.get('valor')}.")
    if pendiente:
        return pendiente
    ejecuciones.append(g.argumentos.get("valor"))
    db.session.commit()
    return responder("Hecho.")


print("\n== 1. Boton y agente por rol ==")
CUENTAS = {
    Rol.CONDUCTOR: ("conductor2@sgds.com", "Conductor123*", "RETELL_AGENTE_CONDUCTOR_ID", "/conductor/"),
    Rol.DESPACHADOR: ("despachador@sgds.com", "Despacho123*", "RETELL_AGENTE_GESTOR_ID", "/admin/"),
    Rol.ADMIN: ("admin@sgds.com", "Admin123*", "RETELL_AGENTE_ADMIN_ID", "/admin/"),
    Rol.CLIENTE: ("cliente@sgds.com", "Cliente123*", "RETELL_AGENTE_CLIENTE_ID", "/portal/"),
}
clientes_web = {rol: sesion(correo, clave) for rol, (correo, clave, _, _) in CUENTAS.items()}

for rol, (correo, _, variable, pagina) in CUENTAS.items():
    c = clientes_web[rol]
    check('id="asistente"' not in c.get(pagina).data.decode(), f"{rol}: sin su agente no ve el boton")
    r = c.post("/asistente/llamada", headers={"X-CSRFToken": csrf_de(c, "/auth/perfil")})
    check(r.status_code == 403, f"{rol}: sin su agente no puede iniciar llamada (403)")

app.config.update(AGENTES)
llamadas = {}
for rol, (correo, _, variable, pagina) in CUENTAS.items():
    c = clientes_web[rol]
    check('id="asistente"' in c.get(pagina).data.decode(), f"{rol}: con su agente ve el boton")
    r = c.post("/asistente/llamada", headers={"X-CSRFToken": csrf_de(c, pagina)})
    llamadas[rol] = (r.get_json() or {}).get("call_id")
    with app.app_context():
        nombre = db.session.query(Usuario).filter_by(correo=correo).one().nombre
    check(r.status_code == 200 and retell.llamadas[-1] == {
              "agent_id": AGENTES[variable],
              "retell_llm_dynamic_variables": {"nombre_usuario": nombre, "fecha_hoy": hoy().isoformat()}},
          f"{rol}: la llamada usa {AGENTES[variable]} con nombre y fecha de hoy")
    with app.app_context():
        guardada = db.session.query(SesionAsistente).filter_by(call_id=llamadas[rol]).one()
        check(guardada.rol == rol, f"{rol}: la sesion guarda el rol")


print("\n== 2. Matriz de permisos ==")
denegadas, permitidas = [], []
for (espacio, nombre), definicion in sorted(FUNCIONES.items()):
    for rol, call_id in llamadas.items():
        estado = funcion(f"{espacio}/{nombre}", call_id)[0]
        (permitidas if rol in definicion.roles else denegadas).append((rol, espacio, nombre, estado))
check(denegadas and all(e == 403 for *_, e in denegadas),
      f"cada rol contra las funciones de los demas: 403 ({len(denegadas)} combinaciones)")
check(permitidas and all(e == 200 for *_, e in permitidas),
      f"cada rol contra sus propias funciones: 200 ({len(permitidas)} combinaciones)")
check(all(f.ruta.startswith("/api/asistente/") for f in FUNCIONES.values()),
      "todas las funciones viven bajo /api/asistente/<espacio>/<funcion>")
check({f.nombre for f in funciones_de_rol(Rol.ADMIN)} >= {"eco-prueba", "accion-prueba"},
      "el admin puede usar las funciones del gestor")
check(not any(f.espacio == "conductor" for f in funciones_de_rol(Rol.ADMIN)),
      "pero no las del conductor")
check(funcion("gestor/no-existe", llamadas[Rol.DESPACHADOR])[0] == 404, "una funcion inexistente: 404")
check(funcion("conductor/eco-prueba", llamadas[Rol.DESPACHADOR])[0] == 404,
      "una funcion pedida en el espacio equivocado: 404")

with app.app_context():
    despachador = db.session.query(Usuario).filter_by(correo="despachador@sgds.com").one()
    despachador_id = despachador.id
    despachador.rol = Rol.ADMIN; db.session.commit()
check(funcion("gestor/eco-prueba", llamadas[Rol.DESPACHADOR])[0] == 403,
      "si el usuario cambia de rol durante la llamada, su sesion deja de servir (403)")
with app.app_context():
    db.session.query(Usuario).filter_by(correo="despachador@sgds.com").one().rol = Rol.DESPACHADOR
    db.session.commit()


print("\n== 3. Confirmacion con estado ==")
CALL_G = llamadas[Rol.DESPACHADOR]
s = funcion("gestor/accion-prueba", CALL_G, {"valor": "A"})
check(mensaje(s) == "Voy a registrar A. ¿confirmas?" and not ejecuciones,
      "sin confirmar=true: resumen que termina en '¿confirmas?' y no ejecuta")
s = funcion("gestor/accion-prueba", CALL_G, {"valor": "A", "confirmar": True})
check(mensaje(s) == "Hecho." and ejecuciones == ["A"], "con confirmar=true tras el resumen: ejecuta")
s = funcion("gestor/accion-prueba", CALL_G, {"valor": "A", "confirmar": True})
check(ejecuciones == ["A"] and mensaje(s).endswith("¿confirmas?"),
      "la misma confirmacion no se puede reutilizar: vuelve a resumir")
funcion("gestor/accion-prueba", CALL_G, {"valor": "A", "confirmar": True})
check(ejecuciones == ["A", "A"], "confirmando el nuevo resumen, ejecuta una vez mas")

s = funcion("gestor/accion-prueba", "call_roles_otra", {"valor": "B", "confirmar": True})
check(s[0] == 403, "un call_id sin sesion no puede confirmar nada")
s = funcion("gestor/accion-prueba", CALL_G, {"valor": "B", "confirmar": True})
check(ejecuciones == ["A", "A"] and mensaje(s) == "Voy a registrar B. ¿confirmas?",
      "confirmar=true sin resumen previo: no ejecuta, resume")

funcion("gestor/accion-prueba", CALL_G, {"valor": "C"})
s = funcion("gestor/accion-prueba", CALL_G, {"valor": "c", "confirmar": True})
check(ejecuciones == ["A", "A"] and mensaje(s) == "Voy a registrar c. ¿confirmas?",
      "argumentos distintos de los resumidos (C vs c): no ejecuta")
s = funcion("gestor/accion-prueba", CALL_G, {"valor": "C", "confirmar": True})
check(ejecuciones == ["A", "A"], "y el resumen anterior quedo reemplazado por el nuevo")

funcion("gestor/accion-prueba", CALL_G, {"valor": "D"})
with app.app_context():
    db.session.query(SesionAsistente).filter_by(call_id=CALL_G).one().confirmacion_vence_en = \
        ahora() - timedelta(seconds=1)
    db.session.commit()
funcion("gestor/accion-prueba", CALL_G, {"valor": "D", "confirmar": True})
check(ejecuciones == ["A", "A"], "un resumen vencido (mas de 3 minutos) no se puede confirmar")
funcion("gestor/accion-prueba", CALL_G, {"valor": "D"})
with app.app_context():
    vence = db.session.query(SesionAsistente).filter_by(call_id=CALL_G).one().confirmacion_vence_en
check(timedelta(minutes=2, seconds=50) < vence - ahora() <= timedelta(minutes=3),
      "el resumen vence a los 3 minutos")

CALL_A = llamadas[Rol.ADMIN]
funcion("gestor/accion-prueba", CALL_A, {"valor": "E"})
funcion("gestor/accion-prueba", CALL_G, {"valor": "E", "confirmar": True})
check(ejecuciones == ["A", "A"], "un resumen de otra llamada no sirve para confirmar en esta")
funcion("gestor/accion-prueba", CALL_A, {"valor": "E", "confirmar": "true"})
check(ejecuciones == ["A", "A", "E"], 'confirmar tambien se acepta como el texto "true"')
funcion("gestor/accion-prueba", CALL_A, {"valor": "F"})
funcion("gestor/accion-prueba", CALL_A, {"valor": "F", "confirmar": "si"})
check(ejecuciones == ["A", "A", "E"], 'pero no "si" ni otros valores')
funcion("gestor/eco-prueba", CALL_A, {})
funcion("gestor/accion-prueba", CALL_A, {"valor": "F", "confirmar": True})
check(ejecuciones == ["A", "A", "E", "F"], "una consulta en medio no borra el resumen pendiente")


print("\n== 4. Acciones del conductor: nada cambia sin confirmar ==")
with app.app_context():
    c2 = db.session.query(Usuario).filter_by(correo="conductor2@sgds.com").one()
    esquina = db.session.query(Cliente).filter_by(nombre="Tienda La Esquina").one()
    ruta = Ruta(codigo="RUT-ROLES-01", fecha=hoy(), estado=EstadoRuta.PLANIFICADA, conductor_id=c2.id)
    db.session.add(ruta); db.session.flush()
    for orden in (1, 2):
        db.session.add(Pedido(codigo=f"ROLES-00{orden}", cliente_id=esquina.id,
                              cliente_nombre="Tienda La Esquina", direccion="Calle 63 #24-18",
                              fecha_despacho=hoy(), estado=EstadoPedido.ASIGNADO, ruta_id=ruta.id,
                              orden_en_ruta=orden, creado_por_id=c2.id))
    db.session.commit()

CALL_C = llamadas[Rol.CONDUCTOR]
antes = huella()
for nombre, args in (("marcar-en-camino", {"orden": 1}), ("registrar-fallo", {"orden": 1, "motivo": "x"}),
                     ("marcar-en-camino", {"orden": 2, "confirmar": True})):
    funcion(f"conductor/{nombre}", CALL_C, args)
check(huella() == antes,
      "resumir, pedir un fallo invalido o confirmar sin resumen no cambia ninguna tabla")
funcion("conductor/marcar-en-camino", CALL_C, {"orden": 2, "confirmar": True})
check(huella() != antes, "confirmar el resumen pendiente si la cambia")
with app.app_context():
    estados = {p.codigo: p.estado for p in db.session.query(Pedido).filter(Pedido.codigo.like("ROLES-%"))}
check(estados == {"ROLES-001": EstadoPedido.ASIGNADO, "ROLES-002": EstadoPedido.EN_RUTA},
      f"solo la parada confirmada paso a EN_RUTA {estados}")
s = funcion("conductor/marcar-en-camino", CALL_C, {"orden": 2})
check("No pude" in mensaje(s) and not mensaje(s).endswith("¿confirmas?"),
      "una transicion imposible se explica sin pedir confirmacion")


print("\n== 5. Busqueda tolerante para voz ==")
with app.app_context():
    d = hoy().strftime("%Y%m%d")
    portal = db.session.query(Cliente).filter_by(nombre="Supermercado El Portal").one()
    esquina = db.session.query(Cliente).filter_by(nombre="Tienda La Esquina").one()
    cliente_portal_id = db.session.query(Usuario).filter_by(correo="cliente@sgds.com").one().cliente.id
    codigos = lambda r: [p.codigo for p in r.elementos]

    check(codigos(buscar_pedidos(f"PED {d[:4]} {d[4:]} 003")) == [f"PED-{d}-003"],
          "pedido por codigo dictado con espacios")
    check(codigos(buscar_pedidos(f"ped-{d}-003")) == [f"PED-{d}-003"], "y en minusculas con guiones")
    check(codigos(buscar_pedidos("el pedido numero 3 de hoy")) == [f"PED-{d}-003"],
          "pedido por numero del dia")
    check(buscar_pedidos("pedido 999").vacio, "un numero del dia que no existe: ninguno")
    check(set(codigos(buscar_pedidos("esquina"))) == {f"PED-{d}-002", "ROLES-001", "ROLES-002"},
          f"pedido por nombre de cliente: los de hoy o abiertos {codigos(buscar_pedidos('esquina'))}")
    varios = buscar_pedidos("tienda")
    check(varios.varias, f"'tienda' coincide con varios pedidos ({len(varios.elementos)})")

    ajeno = f"PED-{d}-002"   # de Tienda La Esquina
    check(cliente_portal_id == portal.id, "la cuenta cliente@sgds.com es de Supermercado El Portal")
    check(buscar_pedidos(ajeno, cliente_id=portal.id).vacio,
          "con cliente_id, un pedido ajeno no aparece ni por su codigo exacto")
    check(buscar_pedidos("esquina", cliente_id=portal.id).vacio, "ni por el nombre del otro cliente")
    check(codigos(buscar_pedidos("pedido 1", cliente_id=portal.id)) == [f"PED-{d}-001"],
          "pero si encuentra los suyos")

    skus = lambda r: [p.sku for p in r.elementos]
    check(skus(buscar_productos("sku 1001")) == ["SKU-1001"], "producto por SKU dictado")
    check(skus(buscar_productos("1003")) == ["SKU-1003"], "producto por el numero del SKU")
    check(skus(buscar_productos("arros")) == ["SKU-1003"], "producto con una palabra mal transcrita")
    check(skus(buscar_productos("detergente dos kilos")) == ["SKU-1005"],
          "producto por la palabra significativa")
    check(buscar_productos("galletas").vacio, "un producto que no existe: ninguno")

    nombres = lambda r: [c.nombre for c in r.elementos]
    check(nombres(buscar_clientes("tienda la esquina")) == ["Tienda La Esquina"],
          "cliente por nombre identico")
    check(nombres(buscar_clientes("esquna")) == ["Tienda La Esquina"], "cliente mal transcrito")
    tiendas = buscar_clientes("tienda")
    check(len(tiendas.elementos) == 3, f"'tienda' coincide con 3 clientes {nombres(tiendas)}")
    check(describir(tiendas, lambda c: c.nombre, "clientes") ==
          "Encontré 3 clientes: Tienda Fontibon, Tienda La Esquina y Tienda Usaquen. ¿Cuál?",
          "varias coincidencias se listan para que el agente pregunte cual")
    muchos = buscar_pedidos("tienda")
    muchos.elementos = muchos.elementos * 4
    texto = describir(muchos, lambda p: p.codigo, "pedidos")
    check(texto.count("PED-") + texto.count("ROLES-") == 5 and "más" in texto,
          "mas de 5 coincidencias: lee 5 y dice cuantas faltan")

    sedes = buscar_sedes(portal)
    check(len(sedes.elementos) == 2, "sin texto, un cliente con dos sedes devuelve ambas")
    check([s.etiqueta for s in buscar_sedes(portal, "toberin").elementos] == ["Sede Toberin"],
          "sede por etiqueta")
    check([s.etiqueta for s in buscar_sedes(portal, "cra 68").elementos] == ["Principal"],
          "sede por direccion")
    check(len(buscar_sedes(esquina).elementos) == 1, "un cliente con una sola sede la devuelve directa")


print("\n== 6. Gestor: consultas ==")
import requests as _requests
from app.services import ruteo as _ruteo
def _sin_red(*args, **kwargs):
    raise _requests.ConnectionError("sin red en las pruebas")
_ruteo._consultar_osrm = _sin_red

def accion(ruta, call_id, args):
    """Accion en dos pasos: resumen y confirmar=true. Un error de negocio vuelve tal cual."""
    resumen = funcion(ruta, call_id, args)
    if not mensaje(resumen).endswith("¿confirmas?"):
        return resumen
    return funcion(ruta, call_id, {**args, "confirmar": True})

NOTA = "Registrado por el asistente de voz"
D = hoy().strftime("%Y%m%d")
with app.app_context():
    from app.services import analitica
    k = analitica.kpis_del_dia()
s = funcion("gestor/resumen-dia", CALL_G)
check(f"Hoy hay {k['total_dia']} pedidos" in mensaje(s) and f"{k['entregados']} entregado" in mensaje(s)
      and f"{k['pendientes']} pendiente" in mensaje(s), f"resumen-dia con los KPIs del dia: {mensaje(s)}")
s = funcion("gestor/pendientes-sin-ruta", CALL_G)
check(f"PED-{D}-006 de Tienda Fontibon" in mensaje(s), "pendientes-sin-ruta lista los de hoy")
s = funcion("gestor/avance-rutas", CALL_G)
check("Andres Molina" in mensaje(s) and "RUT-ROLES-01" in mensaje(s) and "cerradas" in mensaje(s),
      "avance-rutas por conductor")
s = funcion("gestor/buscar-pedido", CALL_G, {"pedido": "pedido 3"})
check(f"PED-{D}-003" in mensaje(s) and "Andres Molina" in mensaje(s) and "Últimos movimientos" in mensaje(s),
      "buscar-pedido: estado, ruta, conductor y bitacora")
s = funcion("gestor/buscar-pedido", CALL_G, {"pedido": "tienda"})
check(mensaje(s).startswith("Encontré") and mensaje(s).endswith("¿Cuál?"),
      "buscar-pedido con varias coincidencias las lista para preguntar cual")
with app.app_context():
    stock_1001 = db.session.query(Producto).filter_by(sku="SKU-1001").one().stock_actual
s = funcion("gestor/stock-producto", CALL_G, {"producto": "caja de bebidas"})
check("SKU-1001" in mensaje(s) and f"{stock_1001} unidades" in mensaje(s), "stock-producto por nombre")
s = funcion("gestor/productos-bajo-minimo", CALL_G)
check("bajo el mínimo" in mensaje(s) and "SKU-1006" in mensaje(s), "productos-bajo-minimo")
s = funcion("gestor/fallidos-hoy", CALL_G)
check(f"PED-{D}-005 de Autoservicio Kennedy, sin motivo registrado" in mensaje(s),
      f"fallidos-hoy lista los de hoy, con su motivo o sin el: {mensaje(s)}")


print("\n== 7. Gestor: crear pedido por voz ==")
app.config["MAKE_WEBHOOK_URL"] = "https://hook.make.invalid/sgds"
with app.app_context():
    db.session.query(Cliente).filter_by(nombre="Supermercado El Portal").one().correo = "compras@portal.invalid"
    db.session.commit()
    pedidos_antes = db.session.query(Pedido).count()
    clientes_antes = db.session.query(Cliente).count()

pedido_voz = {"cliente": "supermercado el portal", "sede": "toberin",
              "productos": [{"producto": "sku 1001", "cantidad": 3}, {"producto": "arroz", "cantidad": 2}],
              "prioridad": 2}
antes = huella()
rechazos = [
    ({**pedido_voz, "cliente": "tienda la esquina"}, "No encontré esa sede de Tienda La Esquina",
     "una sede que no es del cliente"),
    ({**pedido_voz, "productos": [{"producto": "galletas", "cantidad": 1}]}, "No encontré el producto galletas",
     "un producto inexistente"),
    ({**pedido_voz, "cliente": "Ferreteria Nueva"}, "solo se crean pedidos para clientes registrados",
     "un cliente que no existe"),
    ({**pedido_voz, "productos": [{"producto": "arroz", "cantidad": 0}]}, "entero mayor que cero", "cantidad cero"),
    ({**pedido_voz, "productos": [{"producto": "arroz", "cantidad": 2.5}]}, "entero mayor que cero",
     "cantidad no entera"),
    ({**pedido_voz, "productos": [{"producto": "arroz", "cantidad": "dos"}]}, "entero mayor que cero",
     "cantidad en palabras"),
    ({**pedido_voz, "productos": []}, "al menos un producto", "sin productos"),
    ({**pedido_voz, "fecha": "mañana"}, "No entendí la fecha", "una fecha que no es AAAA-MM-DD"),
    ({**pedido_voz, "prioridad": 7}, "prioridad debe ser", "prioridad fuera de 1 a 3"),
]
for args, esperado, que in rechazos:
    s = accion("gestor/crear-pedido", CALL_G, args)
    check(esperado in mensaje(s) and not mensaje(s).endswith("¿confirmas?"), f"rechaza {que}: {mensaje(s)}")
s = funcion("gestor/crear-pedido", CALL_G, {**pedido_voz, "cliente": "tienda"})
check(mensaje(s).startswith("Encontré 3 clientes"), "un cliente ambiguo se pregunta")
s = funcion("gestor/crear-pedido", CALL_G, {**pedido_voz, "sede": ""})
check("Encontré 2 sedes de Supermercado El Portal" in mensaje(s), "sin sede y con dos sedes, pregunta cual")
s = funcion("gestor/crear-pedido", CALL_G, pedido_voz)
check(mensaje(s) == "Voy a crear un pedido para Supermercado El Portal, sede Sede Toberin, en "
      f"Av. Cra 19 #166-30, para el {hoy().strftime('%d/%m/%Y')} con prioridad media: "
      "3 de Caja bebidas 12 und y 2 de Bolsa arroz 5 kg. ¿confirmas?",
      f"la confirmacion lee cliente, sede, fecha, prioridad y cada producto: {mensaje(s)}")
check(huella() == antes, "ningun rechazo ni el resumen crean nada (tampoco clientes ni sedes)")

s = funcion("gestor/crear-pedido", CALL_G, {**pedido_voz, "confirmar": True})
creado = re.search(r"creé el pedido (\S+) para", mensaje(s))
check(creado is not None, f"con confirmar=true crea el pedido: {mensaje(s)}")
with app.app_context():
    nuevo = db.session.query(Pedido).filter_by(codigo=creado[1] if creado else "").first()
    portal_db = db.session.query(Cliente).filter_by(nombre="Supermercado El Portal").one()
    portal_id = portal_db.id
    sede_toberin = next(d for d in portal_db.direcciones if d.etiqueta == "Sede Toberin")
    check(nuevo is not None and nuevo.estado == EstadoPedido.PENDIENTE and nuevo.prioridad == 2
          and nuevo.fecha_despacho == hoy(), "PENDIENTE, prioridad media, fecha de hoy")
    check(nuevo is not None and nuevo.cliente_id == portal_id and nuevo.direccion_id == sede_toberin.id
          and nuevo.direccion == sede_toberin.direccion, "vinculado al cliente y a su sede registrada")
    check(nuevo is not None and sorted((i.producto.sku, i.cantidad) for i in nuevo.items)
          == [("SKU-1001", 3), ("SKU-1003", 2)], "con sus productos y cantidades")
    check(nuevo is not None and nuevo.eventos[0].nota == NOTA
          and nuevo.creado_por.correo == "despachador@sgds.com",
          "la bitacora dice 'Registrado por el asistente de voz' y lo crea el gestor")
    check(db.session.query(Pedido).count() == pedidos_antes + 1
          and db.session.query(Cliente).count() == clientes_antes, "un solo pedido nuevo y ningun cliente nuevo")
    pid_voz, codigo_voz = nuevo.id, nuevo.codigo
funcion("gestor/crear-pedido", CALL_G, {**pedido_voz, "confirmar": True})
with app.app_context():
    check(db.session.query(Pedido).count() == pedidos_antes + 1, "repetir la confirmacion no crea otro")


print("\n== 8. Gestor: demas acciones ==")
# Prioridad
s = accion("gestor/cambiar-prioridad", CALL_G, {"pedido": codigo_voz, "prioridad": 1})
check("prioridad alta" in mensaje(s), f"cambiar-prioridad: {mensaje(s)}")
with app.app_context():
    p = db.session.get(Pedido, pid_voz)
    ultimo = sorted(p.eventos, key=lambda e: e.id)[-1]
    check(p.prioridad == 1 and ultimo.estado_anterior == ultimo.estado_nuevo == EstadoPedido.PENDIENTE
          and "media a alta" in ultimo.nota and NOTA in ultimo.nota, "queda en la bitacora sin cambio de estado")
html = clientes_web[Rol.CLIENTE].get(f"/portal/pedido/{pid_voz}").data.decode()
linea = re.search(r'<ol class="linea-tiempo">(.*?)</ol>', html, re.S)
check(linea is not None and linea.group(1).count("<li>") == 1 and "Prioridad cambiada" not in html,
      "el portal del cliente no muestra el cambio de prioridad como hito")
s = accion("gestor/cambiar-prioridad", CALL_G, {"pedido": f"PED-{D}-003", "prioridad": 1})
check("pendientes o asignados" in mensaje(s), "no se cambia la prioridad de un pedido en ruta")
s = accion("gestor/cambiar-prioridad", CALL_G, {"pedido": codigo_voz, "prioridad": 5})
check("debe ser 1" in mensaje(s), "prioridad fuera de 1 a 3")

# Agregar a ruta (OSRM sin red: respaldo local)
with app.app_context():
    c3 = Usuario(nombre="Sergio Vargas", correo="conductor3@sgds.com", rol=Rol.CONDUCTOR, activo=True)
    c3.establecer_contrasena("Conductor123*")
    db.session.add(c3); db.session.commit()
s = accion("gestor/agregar-a-ruta", CALL_G, {"pedido": codigo_voz, "conductor": "sergio"})
check("no tiene ruta hoy" in mensaje(s) and "pantalla" in mensaje(s), "un conductor sin ruta: se crea en pantalla")
s = funcion("gestor/agregar-a-ruta", CALL_G, {"pedido": codigo_voz, "conductor": "diego"})
check(mensaje(s) == f"Voy a agregar el pedido {codigo_voz} de Supermercado El Portal a la ruta RUT-ROLES-01 "
      "de Diego Pardo y recalcular la secuencia. ¿confirmas?", "agregar-a-ruta resume antes")
s = funcion("gestor/agregar-a-ruta", CALL_G, {"pedido": codigo_voz, "conductor": "diego", "confirmar": True})
check("quedó como parada" in mensaje(s), f"y con confirmar lo agrega: {mensaje(s)}")
with app.app_context():
    p = db.session.get(Pedido, pid_voz)
    ruta = db.session.query(Ruta).filter_by(codigo="RUT-ROLES-01").one()
    ordenes = sorted(x.orden_en_ruta for x in ruta.pedidos)
    check(p.estado == EstadoPedido.ASIGNADO and p.ruta_id == ruta.id, "el pedido queda ASIGNADO en la ruta")
    check(ordenes == list(range(1, len(ruta.pedidos) + 1)), f"la secuencia se recalculo sin huecos {ordenes}")
    check(sorted(p.eventos, key=lambda e: e.id)[-1].nota == f"Asignado a la ruta RUT-ROLES-01. {NOTA}",
          "con su evento en la bitacora")
s = accion("gestor/agregar-a-ruta", CALL_G, {"pedido": codigo_voz, "conductor": "diego"})
check("solo se pueden agregar pedidos pendientes" in mensaje(s), "un pedido ya asignado no se agrega")

# Ruta finalizada: no se reabre por voz.
with app.app_context():
    ruta = db.session.query(Ruta).filter_by(codigo="RUT-ROLES-01").one()
    for x in ruta.pedidos:
        x.estado = EstadoPedido.ENTREGADO
    ruta.estado = EstadoRuta.FINALIZADA
    esquina_id = db.session.query(Cliente).filter_by(nombre="Tienda La Esquina").one().id
    c2_id = db.session.query(Usuario).filter_by(correo="conductor2@sgds.com").one().id
    db.session.add(Pedido(codigo="ROLES-PEND", cliente_id=esquina_id, cliente_nombre="Tienda La Esquina",
                          direccion="Calle 63 #24-18", fecha_despacho=hoy(), estado=EstadoPedido.PENDIENTE,
                          creado_por_id=c2_id))
    db.session.commit()
antes = huella()
s = accion("gestor/agregar-a-ruta", CALL_G, {"pedido": "ROLES-PEND", "conductor": "diego"})
check("ya está finalizada y no se reabre" in mensaje(s) and huella() == antes,
      "con la ruta de hoy finalizada responde que se hace en pantalla y no cambia nada")

# Reintentar un fallido de hoy
s = accion("gestor/reintentar-pedido", CALL_G, {"pedido": f"PED-{D}-005"})
check("quedó asignado para un nuevo intento" in mensaje(s), f"reintentar-pedido: {mensaje(s)}")
with app.app_context():
    p = db.session.query(Pedido).filter_by(codigo=f"PED-{D}-005").one()
    check(p.estado == EstadoPedido.ASIGNADO and sorted(p.eventos, key=lambda e: e.id)[-1].nota == NOTA,
          "el fallido vuelve a ASIGNADO con la nota del asistente")
    viejo = db.session.query(Pedido).filter(Pedido.estado == EstadoPedido.FALLIDO,
                                            Pedido.fecha_despacho < hoy()).first().codigo
s = accion("gestor/reintentar-pedido", CALL_G, {"pedido": viejo})
check("ruta de hoy" in mensaje(s), "un fallido de otro dia no se reintenta por voz")
s = accion("gestor/reintentar-pedido", CALL_G, {"pedido": codigo_voz})
check("Solo se reintenta un pedido fallido" in mensaje(s), "ni un pedido que no esta fallido")

# Entrada de inventario
s = accion("gestor/registrar-entrada", CALL_G, {"producto": "SKU-1001", "cantidad": -3})
check("mayor que cero" in mensaje(s), "una entrada negativa se rechaza")
s = funcion("gestor/registrar-entrada", CALL_G, {"producto": "SKU-1001", "cantidad": 50, "motivo": "Remision 88"})
check(f"pasa de {stock_1001} a {stock_1001 + 50}" in mensaje(s), "la confirmacion lee el stock antes y despues")
funcion("gestor/registrar-entrada", CALL_G, {"producto": "SKU-1001", "cantidad": 50, "motivo": "Remision 88",
                                            "confirmar": True})
with app.app_context():
    from app.models import MovimientoInventario, TipoMovimiento
    prod = db.session.query(Producto).filter_by(sku="SKU-1001").one()
    mov = db.session.query(MovimientoInventario).order_by(MovimientoInventario.id.desc()).first()
    check(prod.stock_actual == stock_1001 + 50 and mov.tipo == TipoMovimiento.ENTRADA and mov.cantidad == 50
          and mov.motivo == f"Remision 88. {NOTA}" and mov.usuario_id == despachador_id,
          "registra la ENTRADA con su trazabilidad")

# Anular: mismo aviso de Make que la pantalla
with app.app_context():
    db.session.add(Pedido(codigo="ROLES-ANULAR", cliente_id=portal_id, cliente_nombre="Supermercado El Portal",
                          direccion="Av. Cra 68 #75-50", fecha_despacho=hoy(), estado=EstadoPedido.PENDIENTE,
                          creado_por_id=c2_id))
    db.session.commit()
s = accion("gestor/anular-pedido", CALL_G, {"pedido": "ROLES-ANULAR", "motivo": ""})
check("Necesito el motivo" in mensaje(s), "anular exige motivo")
envios_antes = len(envios_make)
funcion("gestor/anular-pedido", CALL_G, {"pedido": "ROLES-ANULAR", "motivo": "Duplicado"})
notificaciones.esperar_envios()
check(len(envios_make) == envios_antes, "el resumen de la anulacion no envia avisos")
funcion("gestor/anular-pedido", CALL_G, {"pedido": "ROLES-ANULAR", "motivo": "Duplicado", "confirmar": True})
notificaciones.esperar_envios()
avisos = [e for e in envios_make[envios_antes:] if e.get("codigo") == "ROLES-ANULAR"]
check(len(avisos) == 1 and avisos[0]["tipo"] == "pedido_estado" and avisos[0]["estado"] == "CANCELADO"
      and avisos[0]["motivo"] == "Duplicado",
      "anular por voz envia el mismo aviso pedido_estado CANCELADO con motivo que la pantalla")
with app.app_context():
    p = db.session.query(Pedido).filter_by(codigo="ROLES-ANULAR").one()
    check(p.estado == EstadoPedido.CANCELADO
          and sorted(p.eventos, key=lambda e: e.id)[-1].nota == f"Pedido anulado: Duplicado. {NOTA}",
          "queda CANCELADO con la nota del asistente")
s = accion("gestor/anular-pedido", CALL_G, {"pedido": f"PED-{D}-003", "motivo": "x"})
check("No puedo anular" in mensaje(s), "las mismas reglas que la pantalla: un pedido en ruta no se anula")
s = accion("gestor/crear-pedido", CALL_A, {**pedido_voz, "productos": [{"producto": "SKU-1002", "cantidad": 1}]})
check("creé el pedido" in mensaje(s), "el admin tambien puede usar las funciones del gestor")


print("\n== 9. Admin: analitica ==")
with app.app_context():
    t14 = analitica.tiempo_promedio_entrega(14)
    v30 = analitica.cumplimiento_ventana(30)
    prod14 = analitica.productividad_conductores(14)
    motivos14 = analitica.motivos_fallo(14)
s = funcion("admin/tiempo-promedio-entrega", CALL_A)
check(f"{t14['promedio_min']:.0f} minutos" in mensaje(s) and f"{t14['muestras']} entregas" in mensaje(s)
      and "14 días" in mensaje(s), f"tiempo promedio con el periodo por defecto del panel: {mensaje(s)}")
s = funcion("admin/cumplimiento-ventana", CALL_A, {"dias": 30})
check(f"{v30['dentro']} de {v30['total']}" in mensaje(s) and "30 días" in mensaje(s),
      f"cumplimiento de ventana a 30 dias: {mensaje(s)}")
s = funcion("admin/cumplimiento-ventana", CALL_A, {"dias": 99})
check("14 días" in mensaje(s), "un periodo que el panel no ofrece usa el de por defecto")
s = funcion("admin/productividad-conductores", CALL_A)
check(prod14 and prod14[0]["conductor"] in mensaje(s) and f"{prod14[0]['entregadas']} entregadas" in mensaje(s),
      f"productividad por conductor: {mensaje(s)}")
s = funcion("admin/causas-fallo", CALL_A)
check(motivos14 and f"{motivos14[0]['motivo']}, {motivos14[0]['cantidad']}" in mensaje(s),
      f"causas de fallo de la mas frecuente: {mensaje(s)}")
with app.app_context():
    from app.models import ENDPOINTS_CRITICOS, MedicionRendimiento, UMBRAL_MAXIMO_MS
    critico = next(iter(ENDPOINTS_CRITICOS))
    for ms in (120.0, 180.0, UMBRAL_MAXIMO_MS + 500):
        db.session.add(MedicionRendimiento(endpoint=critico, metodo="GET", estado_http=200,
                                           duracion_ms=ms, registrado_en=ahora()))
    db.session.commit()
    rend = analitica.rendimiento(24)
s = funcion("admin/resumen-rendimiento", CALL_A)
check(f"{rend['muestras']} mediciones" in mensaje(s) and "percentil 95" in mensaje(s)
      and ENDPOINTS_CRITICOS[critico] in mensaje(s),
      f"resumen del panel de rendimiento, con lo que supera el limite: {mensaje(s)}")


print("\n== 10. Admin: ajuste de inventario ==")
app.config["CORREO_OPERACIONES"] = "operaciones@sgds.invalid"
with app.app_context():
    sku = db.session.query(Producto).filter_by(sku="SKU-1001").one()
    stock_antes, minimo = sku.stock_actual, sku.stock_minimo
antes = huella()
s = funcion("admin/ajustar-inventario", CALL_G, {"producto": "SKU-1001", "valor": 5, "motivo": "Conteo"})
check(s[0] == 403, "un ajuste de inventario desde una sesion de gestor: 403")
for args, esperado, que in (
        ({"producto": "SKU-1001", "valor": 5, "motivo": ""}, "Necesito el motivo", "sin motivo"),
        ({"producto": "SKU-1001", "valor": -1, "motivo": "Conteo"}, "mayor o igual a cero", "valor negativo"),
        ({"producto": "SKU-1001", "valor": stock_antes, "motivo": "Conteo"}, "no hay nada que ajustar",
         "al mismo valor"),
        ({"producto": "galletas", "valor": 5, "motivo": "Conteo"}, "No encontré el producto", "producto inexistente")):
    s = accion("admin/ajustar-inventario", CALL_A, args)
    check(esperado in mensaje(s), f"rechaza un ajuste {que}")
s = funcion("admin/ajustar-inventario", CALL_A, {"producto": "SKU-1001", "valor": minimo - 10,
                                                "motivo": "Conteo fisico de octubre"})
check(mensaje(s) == f"Voy a ajustar el inventario de SKU-1001, Caja bebidas 12 und: el stock pasa de "
      f"{stock_antes} a {minimo - 10}, por: Conteo fisico de octubre. ¿confirmas?",
      "la confirmacion lee el stock actual, el nuevo valor y el motivo")
check(huella() == antes, "ni el 403, ni los rechazos, ni el resumen cambian datos")
envios_antes = len(envios_make)
s = funcion("admin/ajustar-inventario", CALL_A, {"producto": "SKU-1001", "valor": minimo - 10,
                                                "motivo": "Conteo fisico de octubre", "confirmar": True})
notificaciones.esperar_envios()
with app.app_context():
    from app.models import MovimientoInventario, TipoMovimiento
    prod = db.session.query(Producto).filter_by(sku="SKU-1001").one()
    mov = db.session.query(MovimientoInventario).order_by(MovimientoInventario.id.desc()).first()
    check(prod.stock_actual == minimo - 10 and mov.tipo == TipoMovimiento.AJUSTE
          and mov.motivo == f"Conteo fisico de octubre. {NOTA}", "registra el AJUSTE con el motivo y la nota")
stock_bajo = [e for e in envios_make[envios_antes:] if e.get("tipo") == "stock_bajo"]
check(len(stock_bajo) == 1 and stock_bajo[0]["sku"] == "SKU-1001" and stock_bajo[0]["pedido"] is None
      and stock_bajo[0]["negativo"] is False,
      "al cruzar el minimo envia el mismo aviso stock_bajo que la pantalla")

nombres = [f"{f.espacio}/{f.nombre}" for f in FUNCIONES.values()]
check(not [n for n in nombres if re.search(r"usuario|rol|contrasena|clave|activar|cuenta", n)],
      "no hay ninguna funcion de voz sobre usuarios, roles, contrasenas o cuentas")
check(not any(f.espacio == "admin" and Rol.DESPACHADOR in f.roles for f in FUNCIONES.values()),
      "ninguna funcion del espacio admin admite al gestor")


print("\n== 11. Cliente: consultas y aislamiento ==")
CALL_CL = llamadas[Rol.CLIENTE]
NO_ENCONTRADO = "No encontré ese pedido entre los tuyos."
respuestas_cliente = []
def cliente_voz(nombre, args=None):
    s = funcion(f"cliente/{nombre}", CALL_CL, args)
    respuestas_cliente.append(mensaje(s))
    return s

with app.app_context():
    from app.models import PruebaEntrega
    portal_db = db.session.query(Cliente).filter_by(nombre="Supermercado El Portal").one()
    portal_id = portal_db.id
    abiertos_portal = {p.codigo for p in db.session.query(Pedido).filter(
        Pedido.cliente_id == portal_id, Pedido.estado.in_(EstadoPedido.ABIERTOS))}
    ajeno_pendiente = db.session.query(Pedido).filter(
        Pedido.cliente_id != portal_id, Pedido.estado == EstadoPedido.PENDIENTE).first().codigo
    # Un fallido propio con motivo y un pendiente propio para cancelar.
    fallido = Pedido(codigo="ROLES-CLI-FAL", cliente_id=portal_id, cliente_nombre="Supermercado El Portal",
                     direccion="Av. Cra 68 #75-50", fecha_despacho=hoy(), estado=EstadoPedido.FALLIDO,
                     creado_por_id=c2_id)
    propio = Pedido(codigo="ROLES-CLI-PEND", cliente_id=portal_id, cliente_nombre="Supermercado El Portal",
                    direccion="Av. Cra 68 #75-50", fecha_despacho=hoy(), estado=EstadoPedido.PENDIENTE,
                    creado_por_id=c2_id)
    db.session.add_all([fallido, propio]); db.session.flush()
    db.session.add(PruebaEntrega(pedido_id=fallido.id, motivo_fallo="Establecimiento cerrado",
                                 registrado_en=ahora()))
    db.session.commit()
    abiertos_portal.add("ROLES-CLI-PEND")

s = cliente_voz("pedidos-en-curso")
leidos = set(re.findall(r"\b(?:PED-\d{8}-\d{3}|ROLES-[A-Z-]+|AUTO-[A-Z0-9-]+)\b", mensaje(s)))
check(leidos and leidos <= abiertos_portal, f"pedidos-en-curso solo lista pedidos abiertos propios {sorted(leidos)}")
s = cliente_voz("estado-pedido", {"pedido": f"PED-{D}-001"})
check(mensaje(s).startswith(f"Tu pedido PED-{D}-001: programado para despacho.") and "Historial:" in mensaje(s),
      f"estado-pedido con la traduccion del portal: {mensaje(s)}")
s = cliente_voz("ventana-entrega", {"pedido": f"PED-{D}-001"})
check("ventana" in mensaje(s) and hoy().strftime("%d/%m/%Y") in mensaje(s), "ventana-entrega")
s = cliente_voz("motivo-fallo", {"pedido": "ROLES-CLI-FAL"})
check(mensaje(s) == "La entrega de tu pedido ROLES-CLI-FAL no se logró por: Establecimiento cerrado.",
      "motivo-fallo de un pedido propio")
s = cliente_voz("motivo-fallo", {"pedido": f"PED-{D}-001"})
check("no tiene una entrega fallida" in mensaje(s), "motivo-fallo de un pedido que no fallo")
s = cliente_voz("mis-sedes")
check("Principal, en Av. Cra 68 #75-50" in mensaje(s) and "Sede Toberin" in mensaje(s), "mis-sedes")

for nombre in ("estado-pedido", "ventana-entrega", "motivo-fallo"):
    respuestas = {mensaje(cliente_voz(nombre, {"pedido": codigo}))
                  for codigo in (f"PED-{D}-002", f"PED-{D}-999", "pedido 2", ajeno_pendiente)}
    check(respuestas == {NO_ENCONTRADO},
          f"{nombre}: un pedido ajeno se responde igual que uno inexistente ({respuestas})")


print("\n== 12. Cliente: cancelar y solicitar contacto ==")
antes = huella()
for args in ({"pedido": ajeno_pendiente, "motivo": "x"}, {"pedido": ajeno_pendiente, "motivo": "x", "confirmar": True}):
    s = cliente_voz("cancelar-pedido", args)
    check(mensaje(s) == NO_ENCONTRADO, "cancelar un pedido ajeno: 'no encontrado', incluso con confirmar=true")
s = cliente_voz("cancelar-pedido", {"pedido": f"PED-{D}-001", "motivo": "Ya no lo necesito"})
check("debes contactar al gestor" in mensaje(s) and not mensaje(s).endswith("¿confirmas?"),
      "un pedido propio ya programado: debe contactar al gestor")
s = cliente_voz("cancelar-pedido", {"pedido": "ROLES-CLI-PEND", "motivo": ""})
check("Necesito el motivo" in mensaje(s), "cancelar exige motivo")
s = cliente_voz("cancelar-pedido", {"pedido": "ROLES-CLI-PEND", "motivo": "Ya no lo necesito"})
check(mensaje(s) == f"Voy a cancelar tu pedido ROLES-CLI-PEND del {hoy().strftime('%d/%m/%Y')} por: "
      "Ya no lo necesito. ¿confirmas?", "la cancelacion resume antes")
check(huella() == antes, "nada de lo anterior cambia datos")
envios_antes = len(envios_make)
s = cliente_voz("cancelar-pedido", {"pedido": "ROLES-CLI-PEND", "motivo": "Ya no lo necesito", "confirmar": True})
notificaciones.esperar_envios()
with app.app_context():
    p = db.session.query(Pedido).filter_by(codigo="ROLES-CLI-PEND").one()
    check(p.estado == EstadoPedido.CANCELADO
          and sorted(p.eventos, key=lambda e: e.id)[-1].nota == f"Pedido anulado: Ya no lo necesito. {NOTA}",
          "con confirmar=true cancela su pedido pendiente, con la nota del asistente")
avisos = [e for e in envios_make[envios_antes:] if e.get("codigo") == "ROLES-CLI-PEND"]
check(len(avisos) == 1 and avisos[0]["estado"] == "CANCELADO" and avisos[0]["motivo"] == "Ya no lo necesito",
      "y envia el mismo aviso pedido_estado CANCELADO")

with app.app_context():
    from app.models import SolicitudContacto
    portal_db = db.session.get(Cliente, portal_id)
    portal_db.telefono = "3115550111"
    db.session.commit()
s = cliente_voz("solicitar-contacto", {"motivo": "Cancelar el pedido de hoy"})
check(mensaje(s) == "Voy a pedir que el gestor logístico te contacte por: Cancelar el pedido de hoy. "
      "Te contactará por 3115550111 y compras@portal.invalid. ¿confirmas?", f"solicitar-contacto resume: {mensaje(s)}")
with app.app_context():
    check(db.session.query(SolicitudContacto).count() == 0, "el resumen no registra nada")
envios_antes = len(envios_make)
cliente_voz("solicitar-contacto", {"motivo": "Cancelar el pedido de hoy", "confirmar": True})
notificaciones.esperar_envios()
with app.app_context():
    sol = db.session.query(SolicitudContacto).one()
    check(sol.cliente_id == portal_id and sol.motivo == "Cancelar el pedido de hoy" and not sol.atendida
          and sol.telefono == "3115550111" and sol.correo == "compras@portal.invalid",
          "con confirmar=true registra la solicitud con el contacto del cliente")
    sol_id = sol.id
eventos = [e for e in envios_make[envios_antes:] if e.get("tipo") == "solicitud_contacto"]
check(len(eventos) == 1 and set(eventos[0]) == {"tipo", "cliente", "correo", "telefono", "motivo", "hora",
                                                 "correo_destino"}
      and eventos[0]["correo_destino"] == "operaciones@sgds.invalid"
      and eventos[0]["cliente"] == "Supermercado El Portal",
      f"y envia a Make el evento solicitud_contacto {eventos[0] if eventos else None}")

app.config["CORREO_OPERACIONES"] = ""
envios_antes = len(envios_make)
cliente_voz("solicitar-contacto", {"motivo": "Cambiar la ventana"})
cliente_voz("solicitar-contacto", {"motivo": "Cambiar la ventana", "confirmar": True})
notificaciones.esperar_envios()
with app.app_context():
    check(db.session.query(SolicitudContacto).count() == 2, "sin CORREO_OPERACIONES la solicitud se registra igual")
check(not [e for e in envios_make[envios_antes:] if e.get("tipo") == "solicitud_contacto"],
      "pero no se envia el aviso interno")

prohibidos = ["RUT-", "Andres Molina", "Diego Pardo", "Sergio Vargas", "SKU-", "Tienda La Esquina",
              "Tienda Fontibon", "Autoservicio Kennedy", "stock", "conductor", "ruta "]
filtrado = [(p, r) for r in respuestas_cliente for p in prohibidos if p.lower() in r.lower()]
check(not filtrado, f"ninguna respuesta del cliente expone rutas, conductores, stock ni otros clientes {filtrado[:2]}")


print("\n== 13. Solicitudes de contacto: gestor y admin ==")
s = funcion("gestor/solicitudes-contacto-pendientes", CALL_G)
check("Hay 2 solicitudes de contacto pendientes" in mensaje(s) and "Cancelar el pedido de hoy" in mensaje(s)
      and "3115550111" in mensaje(s), f"el gestor las consulta por voz: {mensaje(s)}")
gestor_web = clientes_web[Rol.DESPACHADOR]
html = gestor_web.get("/admin/solicitudes/").data.decode()
check("Cancelar el pedido de hoy" in html and "Marcar atendida" in html, "y las ve en pantalla")
check('href="/admin/solicitudes/"' in gestor_web.get("/admin/").data.decode(), "con su enlace en la navegacion")
check(clientes_web[Rol.ADMIN].get("/admin/solicitudes/").status_code == 200, "el admin tambien")
r = clientes_web[Rol.CLIENTE].get("/admin/solicitudes/")
check(r.status_code in (302, 403), f"el cliente no puede verlas ({r.status_code})")
r = clientes_web[Rol.CONDUCTOR].get("/admin/solicitudes/")
check(r.status_code in (302, 403), f"ni el conductor ({r.status_code})")
r = gestor_web.post(f"/admin/solicitudes/{sol_id}/atender", data={})
with app.app_context():
    check(not db.session.get(SolicitudContacto, sol_id).atendida, "marcar atendida sin token CSRF no hace nada")
token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html).group(1)
r = gestor_web.post(f"/admin/solicitudes/{sol_id}/atender", data={"csrf_token": token}, follow_redirects=True)
with app.app_context():
    sol = db.session.get(SolicitudContacto, sol_id)
    check(sol.atendida and sol.atendida_por.correo == "despachador@sgds.com" and sol.atendida_en is not None,
          "el gestor la marca como atendida, con quien y cuando")
s = funcion("gestor/solicitudes-contacto-pendientes", CALL_G)
check(mensaje(s).startswith("Hay 1 solicitud de contacto pendiente: ") and "Cambiar la ventana" in mensaje(s)
      and "Cancelar el pedido de hoy" not in mensaje(s), "y deja de figurar como pendiente")
check("Atendida" in gestor_web.get("/admin/solicitudes/?estado=atendidas").data.decode(),
      "el filtro de atendidas la muestra")

with app.app_context():
    huerfano = Usuario(nombre="Cuenta sin cliente", correo="sincliente@sgds.com", rol=Rol.CLIENTE, activo=True)
    huerfano.establecer_contrasena("Cliente123*")
    db.session.add(huerfano); db.session.flush()
    db.session.add(SesionAsistente(call_id="call_sin_cliente", usuario_id=huerfano.id, rol=Rol.CLIENTE,
                                   vence_en=ahora() + timedelta(minutes=10)))
    db.session.commit()
s = funcion("cliente/pedidos-en-curso", "call_sin_cliente")
check(s[0] == 200 and "no está vinculada a un cliente" in mensaje(s),
      "una cuenta de cliente sin cliente vinculado no ve nada")

from migraciones.m004_solicitudes_contacto import aplicar as aplicar_m004
with app.app_context():
    check(aplicar_m004(verboso=False) is False, "m004 sobre una base que ya tiene la tabla no hace nada")


print("\n== Final. Migracion de las columnas de confirmacion ==")
from migraciones.m003_confirmacion_asistente import aplicar
with app.app_context():
    check(aplicar(verboso=False) == [], "sobre una base al dia no agrega nada")
    # Tabla como la dejo m002, sin las columnas nuevas.
    SesionAsistente.__table__.drop(db.engine)
    antigua = Table("sesiones_asistente", MetaData(),
                    Column("id", Integer, primary_key=True),
                    Column("call_id", String(64), unique=True, nullable=False),
                    Column("usuario_id", Integer, nullable=False),
                    Column("rol", String(20), nullable=False),
                    Column("creada_en", DateTime, nullable=False),
                    Column("vence_en", DateTime, nullable=False))
    antigua.create(db.engine)
    check(aplicar(verboso=False) == ["confirmacion_firma", "confirmacion_vence_en"],
          "sobre la tabla de m002 agrega las dos columnas")
    check(aplicar(verboso=False) == [], "volver a ejecutarla no cambia nada")
    c2_id = db.session.query(Usuario).filter_by(correo="conductor2@sgds.com").one().id
    db.session.add(SesionAsistente(call_id="call_migrada", usuario_id=c2_id, rol=Rol.CONDUCTOR,
                                   vence_en=ahora() + timedelta(minutes=10),
                                   confirmacion_firma="x" * 64, confirmacion_vence_en=ahora()))
    db.session.commit()
    check(db.session.query(SesionAsistente).filter_by(call_id="call_migrada").one().confirmacion_firma
          == "x" * 64, "y el modelo puede usarlas")


print("\n" + "=" * 55)
print("RESULTADO: " + ("TODAS LAS PRUEBAS PASARON" if not fallos else f"{len(fallos)} FALLAS"))
for f in fallos: print("   - " + f)
sys.exit(1 if fallos else 0)
