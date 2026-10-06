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
from app.models import (Cliente, EstadoPedido, EstadoRuta, Pedido, Rol, Ruta, SesionAsistente,
                        Usuario)
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


print("\n== 6. Migracion de las columnas de confirmacion ==")
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
