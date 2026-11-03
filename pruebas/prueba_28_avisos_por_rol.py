"""Avisos de Make al correo de cada perfil segun su rol, y el correo de avisos de cada cuenta.

- `usuarios.correo_avisos`: opcional (vacio = el de inicio de sesion), en pantalla
  (Usuarios y Perfil) y por voz (admin/usuarios crear y editar), con auditoria.
- Destinatarios: stock_bajo y solicitud_contacto a los gestores activos;
  resumen_diario a gestores y administradores; accion_asistente a los
  administradores; credencial_usuario y restablecimiento al correo de inicio de
  sesion. Un evento por destinatario; CORREO_OPERACIONES solo de respaldo.
- ruta_asignada al conductor en cada operacion de una ruta de hoy o futura, en
  pantalla y por voz, con los campos exactos que espera Make.
- La siembra no envia avisos y toma el correo de avisos de las cuentas demo del .env.

Corre sin internet: Make y Retell se simulan.
"""

import json
import pathlib
import re
import sys
from datetime import time, timedelta
from types import SimpleNamespace

import requests as _requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _preparar  # noqa: E402  Primero: base de pruebas, sin Make, Retell ni red externa.
from retell.lib.webhook_auth import symmetric

from run import app
from app.extensions import db
from app.models import (Auditoria, Cliente, EstadoPedido, Pedido, Producto, Rol, Ruta, SesionAsistente,
                        Usuario, VIGENCIA_SESION)
from app.services import notificaciones, ruteo
from app.services.asistente import huella_sesion
from app.services.auditoria import auditar_pantalla, registrar_auditoria
from app.services.solicitudes import registrar_solicitud
from app.tiempo import ahora, hoy

fallos = []
def check(c, m):
    print(("  OK   " if c else "  FALLA") + f" {m}")
    if not c: fallos.append(m)

CLAVE = "clave-retell-avisos"
OPERACIONES = "operaciones@sgds.invalid"
URL_SGDS = "https://hook.make.invalid/sgds"
URL_PRIVADO = "https://hook.make.invalid/privado"
AVISOS_DEMO = {"SEMILLA_CORREO_ADMIN": "laura.avisos@ejemplo.com",
               "SEMILLA_CORREO_GESTOR": "carlos.avisos@ejemplo.com",
               "SEMILLA_CORREO_CONDUCTOR": "andres.avisos@ejemplo.com"}

envios = []
notificaciones.requests.post = lambda url, json=None, timeout=None, headers=None: (
    envios.append((url, json)), SimpleNamespace(raise_for_status=lambda: None))[1]
def _sin_red(*_, **__):
    raise _requests.ConnectionError("sin red en las pruebas")
ruteo._consultar_osrm = _sin_red


print("\n== 1. La siembra no avisa y toma los correos de avisos del .env ==")
app.config.update(MAKE_WEBHOOK_URL=URL_SGDS, CORREO_OPERACIONES=OPERACIONES)
_preparar.reiniciar_base(**AVISOS_DEMO)
notificaciones.esperar_envios()
check(not envios, f"con MAKE_WEBHOOK_URL definido, sembrar no envia ningun aviso ({len(envios)})")
with app.app_context():
    cuentas = {u.correo: u for u in db.session.query(Usuario)}
    check(cuentas["admin@sgds.com"].correo_avisos == AVISOS_DEMO["SEMILLA_CORREO_ADMIN"]
          and cuentas["despachador@sgds.com"].correo_avisos == AVISOS_DEMO["SEMILLA_CORREO_GESTOR"]
          and cuentas["conductor1@sgds.com"].correo_avisos == AVISOS_DEMO["SEMILLA_CORREO_CONDUCTOR"],
          "admin, gestor y conductor1 quedan con el correo de avisos de su variable")
    check(cuentas["conductor2@sgds.com"].correo_avisos is None
          and cuentas["conductor2@sgds.com"].correo_para_avisos == "conductor2@sgds.com",
          "sin variable, los avisos van al correo de inicio de sesion")
fuente = (_preparar.RAIZ / "seed.py").read_text(encoding="utf-8")
check(not re.search(r"@(gmail|hotmail|outlook|yahoo)\.", fuente), "ningun correo personal en seed.py")

app.config.update(RETELL_API_KEY=CLAVE, MAKE_WEBHOOK_URL=URL_SGDS, MAKE_WEBHOOK_KEY="",
                  MAKE_WEBHOOK_RESUMEN_URL=URL_PRIVADO, MAKE_WEBHOOK_CREDENCIALES_URL=URL_PRIVADO,
                  CORREO_OPERACIONES=OPERACIONES, WTF_CSRF_ENABLED=False, URL_PUBLICA="https://sgds.ejemplo.com")


# ---- Utilidades ----
def avisos(tipo, desde=0):
    notificaciones.esperar_envios()
    return [e for _, e in envios[desde:] if e.get("tipo") == tipo]

def destinos(tipo, desde=0):
    return sorted(e["correo_destino"] for e in avisos(tipo, desde))

def navegador(correo, clave):
    c = app.test_client()
    c.post("/auth/login", data={"correo": correo, "contrasena": clave})
    return c

def alertas(r):
    return " ".join(re.findall(r'class="alerta[^"]*"[^>]*>\s*(.*?)\s*</', r.get_data(as_text=True), re.S))

retell = app.test_client()
def dice(ruta, call_id, args=None):
    cuerpo = json.dumps({"call": {"call_id": call_id}, "args": args or {}}, ensure_ascii=False)
    r = retell.post(f"/api/asistente/{ruta}", data=cuerpo.encode(),
                    headers={"Content-Type": "application/json",
                             "X-Retell-Signature": symmetric["sign"](cuerpo, CLAVE)})
    return (r.get_json(silent=True) or {}).get("mensaje", "")

def confirma(ruta, call_id, args):
    resumen = dice(ruta, call_id, args)
    if not resumen.endswith("¿confirmas?"):
        return resumen, None
    return resumen, dice(ruta, call_id, {**args, "confirmar": True})

def llamada(correo, call_id):
    with app.app_context():
        u = db.session.query(Usuario).filter_by(correo=correo).one()
        db.session.add(SesionAsistente(call_id=call_id, usuario_id=u.id, rol=u.rol, huella_sesion=huella_sesion(u),
                                       vence_en=ahora() + VIGENCIA_SESION))
        db.session.commit()

def usuario(correo):
    with app.app_context():
        u = db.session.query(Usuario).filter_by(correo=correo).one_or_none()
        if u is not None:
            db.session.expunge(u)
        return u

A, G = "call_avisos_admin", "call_avisos_gestor"
# Despues del inicio de sesion web: una llamada vale lo que la sesion que la abrio.
admin = navegador("admin@sgds.com", "Admin123*")
llamada("admin@sgds.com", A)
llamada("despachador@sgds.com", G)


print("\n== 2. Correo de avisos en la pantalla de Usuarios ==")
def alta(**datos):
    base = {"nombre": "Paula Vega", "correo": "paula@sgds.com", "rol": Rol.CONDUCTOR, "cliente_id": "0",
            "activo": "y", "contrasena": "Temporal#2026", "exigir_cambio": "y"}
    return admin.post("/admin/usuarios/nuevo", data={**base, **datos}, follow_redirects=True)

r = alta(correo_avisos="no-es-correo")
check(usuario("paula@sgds.com") is None and "correo de avisos no es válido" in r.get_data(as_text=True),
      "un correo de avisos invalido no crea la cuenta y dice por que")
alta(correo_avisos="  Paula.Avisos@Ejemplo.Com ")
paula = usuario("paula@sgds.com")
check(paula is not None and paula.correo_avisos == "paula.avisos@ejemplo.com",
      "al crear se guarda el correo de avisos, normalizado")
with app.app_context():
    alta_audit = db.session.query(Auditoria).filter_by(accion="Crear usuario").order_by(Auditoria.id.desc()).first()
check("paula.avisos@ejemplo.com" in (alta_audit.detalle or ""), "y queda en la auditoria del alta")
html = admin.get(f"/admin/usuarios/{paula.id}/editar").get_data(as_text=True)
check('name="correo_avisos"' in html and "paula.avisos@ejemplo.com" in html, "editar muestra el campo con su valor")
admin.post(f"/admin/usuarios/{paula.id}/editar", data={
    "nombre": "Paula Vega", "correo": "paula@sgds.com", "correo_avisos": "paula.ruta@ejemplo.com",
    "rol": Rol.CONDUCTOR, "cliente_id": "0", "activo": "y"})
check(usuario("paula@sgds.com").correo_avisos == "paula.ruta@ejemplo.com", "editar lo cambia")
with app.app_context():
    edicion = db.session.query(Auditoria).filter_by(accion="Editar usuario").order_by(Auditoria.id.desc()).first()
check("correo avisos" in edicion.resumen and "paula.ruta@ejemplo.com" in (edicion.detalle or ""),
      "el cambio queda en la auditoria como cualquier campo de la cuenta")


print("\n== 3. El propio correo de avisos, en Perfil ==")
diego = navegador("conductor2@sgds.com", "Conductor123*")
check("Correo para avisos" in diego.get("/auth/perfil").get_data(as_text=True), "Perfil muestra la seccion")
r = diego.post("/auth/perfil/avisos", data={"correo_avisos": "diego.avisos@ejemplo.com"}, follow_redirects=True)
check(usuario("conductor2@sgds.com").correo_avisos == "diego.avisos@ejemplo.com"
      and "diego.avisos@ejemplo.com" in alertas(r), f"lo guarda y lo confirma: {alertas(r)}")
with app.app_context():
    propio = db.session.query(Auditoria).filter_by(accion="Cambiar correo de avisos propio").one()
check(propio.usuario_nombre == "Diego Pardo" and propio.origen == "pantalla", "y queda en la auditoria")
r = diego.post("/auth/perfil/avisos", data={"correo_avisos": "nada@"}, follow_redirects=True)
check("no es válido" in alertas(r) and usuario("conductor2@sgds.com").correo_avisos == "diego.avisos@ejemplo.com",
      "un correo invalido se rechaza sin cambiar nada")
diego.post("/auth/perfil/avisos", data={"correo_avisos": ""})
check(usuario("conductor2@sgds.com").correo_avisos is None, "vacio vuelve al correo de inicio de sesion")
diego.post("/auth/perfil/avisos", data={"correo_avisos": "diego.avisos@ejemplo.com"})


print("\n== 4. Correo de avisos por voz (admin/usuarios) ==")
resumen, final = confirma("admin/usuarios", A, {"operacion": "crear", "nombre": "Nora Diaz", "correo": "nora@sgds.com",
                                                "rol": "conductor", "correo_avisos": "nora.avisos@ejemplo.com"})
check("Sus avisos le llegarán a nora.avisos@ejemplo.com" in resumen and final and final.startswith("Listo"),
      f"crear lee el correo de avisos en el resumen: {resumen}")
check(usuario("nora@sgds.com").correo_avisos == "nora.avisos@ejemplo.com", "y lo guarda")
resumen, final = confirma("admin/usuarios", A, {"operacion": "editar", "usuario": "nora",
                                                "correo_avisos": "nora.ruta@ejemplo.com"})
check("el correo de avisos de nora.avisos@ejemplo.com a nora.ruta@ejemplo.com" in resumen
      and usuario("nora@sgds.com").correo_avisos == "nora.ruta@ejemplo.com", f"editar lo cambia: {resumen}")
with app.app_context():
    voz = db.session.query(Auditoria).filter_by(accion="Editar usuario", origen="voz").order_by(Auditoria.id.desc()).first()
check(voz is not None and voz.call_id == A, "el cambio por voz queda en la auditoria con su llamada")
check("no es válido" in dice("admin/usuarios", A, {"operacion": "editar", "usuario": "nora", "correo_avisos": "nada@"}),
      "un correo invalido se rechaza con la misma regla de la pantalla")
confirma("admin/usuarios", A, {"operacion": "editar", "usuario": "nora", "correo_avisos": "ninguno"})
check(usuario("nora@sgds.com").correo_avisos is None, "'ninguno' lo quita")
check("Sus avisos le llegan a" in dice("admin/usuarios", A, {"operacion": "consultar", "usuario": "nora"}),
      "consultar dice adonde le llegan los avisos")


print("\n== 5. Cada aviso interno, al rol que lo atiende ==")
with app.app_context():
    g2 = Usuario(nombre="Gestor Dos", correo="gestor2@sgds.com", rol=Rol.DESPACHADOR, activo=True,
                 correo_avisos="gestor2.avisos@ejemplo.com")
    g2.establecer_contrasena("Gestor#2026")
    db.session.add(g2)
    db.session.commit()
GESTORES = sorted([AVISOS_DEMO["SEMILLA_CORREO_GESTOR"], "gestor2.avisos@ejemplo.com"])
ADMINS = [AVISOS_DEMO["SEMILLA_CORREO_ADMIN"]]

def stock_bajo():
    inicio = len(envios)
    with app.test_request_context():
        producto = db.session.query(Producto).filter_by(sku="SKU-1001").one()
        producto.stock_actual = producto.stock_minimo
        notificaciones.aviso_stock(producto, producto.stock_minimo + 5)
        db.session.commit()
    return destinos("stock_bajo", inicio)

check(stock_bajo() == GESTORES, "stock_bajo: un evento por gestor activo, a su correo de avisos")
inicio = len(envios)
with app.test_request_context():
    portal = db.session.query(Cliente).filter_by(nombre="Supermercado El Portal").one()
    registrar_solicitud(portal, usuario("cliente@sgds.com").id, "Cambiar la ventana")
    db.session.commit()
check(destinos("solicitud_contacto", inicio) == GESTORES, "solicitud_contacto: a los gestores activos")
inicio = len(envios)
with app.test_request_context():
    registrar_auditoria("Prueba", "configuracion", "Una accion con correo.", avisar=True)
    db.session.commit()
check(destinos("accion_asistente", inicio) == ADMINS, "accion_asistente: a los administradores activos")
inicio = len(envios)
with app.test_request_context():
    auditar_pantalla("Prueba sin correo", "pedido", "Una accion sin correo.")
    db.session.commit()
check(not avisos("accion_asistente", inicio), "avisar=False sigue sin enviar el correo")
inicio = len(envios)
admin.post("/admin/resumen/enviar")
check(destinos("resumen_diario", inicio) == sorted(GESTORES + ADMINS),
      "resumen_diario: a los gestores y los administradores activos")
inicio = len(envios)
with app.test_request_context():
    nora = db.session.query(Usuario).filter_by(correo="nora@sgds.com").one()
    nora.correo_avisos = "nora.avisos@ejemplo.com"
    notificaciones.aviso_credencial_usuario(nora, "Clave#2026x", notificaciones.ACCION_RESTABLECIMIENTO, nora)
    notificaciones.aviso_restablecimiento(nora, "https://sgds.ejemplo.com/auth/restablecer/x", 30)
    db.session.commit()
check([e["correo_destino"] for e in avisos("credencial_usuario", inicio)] == ["nora@sgds.com"],
      "credencial_usuario: al correo de inicio de sesion, no al de avisos")

with app.app_context():
    db.session.query(Usuario).filter_by(correo="gestor2@sgds.com").one().activo = False
    db.session.commit()
check(stock_bajo() == [AVISOS_DEMO["SEMILLA_CORREO_GESTOR"]], "una cuenta inactiva no recibe avisos")
with app.app_context():
    db.session.query(Usuario).filter(Usuario.rol == Rol.DESPACHADOR).update({"activo": False})
    db.session.commit()
check(stock_bajo() == [OPERACIONES], "sin gestores activos, el aviso va al respaldo CORREO_OPERACIONES")
app.config["CORREO_OPERACIONES"] = ""
check(stock_bajo() == [], "y sin respaldo no sale")
app.config["CORREO_OPERACIONES"] = OPERACIONES
with app.app_context():
    db.session.query(Usuario).filter(Usuario.correo == "despachador@sgds.com").update({"activo": True})
    db.session.commit()


print("\n== 6. ruta_asignada al conductor, por voz ==")
CAMPOS_RUTA = {"tipo", "conductor", "correo_destino", "ruta", "fecha", "paradas", "accion", "detalle_ruta",
               "hora", "fecha_hora", "realizado_por", "rol_realizado_por", "origen", "realizado_por_publico"}
with app.app_context():
    sofia = Usuario(nombre="Sofia Ruiz", correo="sofia@sgds.com", rol=Rol.CONDUCTOR, activo=True,
                    correo_avisos="sofia.ruta@ejemplo.com")
    sofia.establecer_contrasena("Conductor#2026")
    db.session.add(sofia)
    portal = db.session.query(Cliente).filter_by(nombre="Supermercado El Portal").one()
    gestor_id = db.session.query(Usuario).filter_by(correo="despachador@sgds.com").one().id
    def pendiente(codigo, fecha, lat, lng):
        db.session.add(Pedido(codigo=codigo, cliente_id=portal.id, cliente_nombre=portal.nombre,
                              direccion="Av. Cra 68 #75-50", ciudad="Bogota", fecha_despacho=fecha,
                              estado=EstadoPedido.PENDIENTE, creado_por_id=gestor_id, latitud=lat, longitud=lng,
                              ventana_inicio=time(8, 0), ventana_fin=time(17, 0)))
    D, M = hoy().strftime("%Y%m%d"), (hoy() + timedelta(days=1)).strftime("%Y%m%d")
    for i, (lat, lng) in enumerate(((4.67, -74.08), (4.65, -74.07), (4.64, -74.06)), start=1):
        pendiente(f"PED-{D}-90{i}", hoy(), lat, lng)
        pendiente(f"PED-{M}-90{i}", hoy() + timedelta(days=1), lat, lng)
    pendiente(f"PED-{D}-990", hoy() - timedelta(days=1), 4.66, -74.07)
    db.session.commit()

def ruta_evento(inicio):
    eventos = avisos("ruta_asignada", inicio)
    return eventos[0] if len(eventos) == 1 else {"cantidad": len(eventos)}

inicio = len(envios)
_, final = confirma("gestor/rutas", G, {"operacion": "crear", "conductor": "Sofia Ruiz", "fecha": hoy().isoformat(),
                                        "pedidos": [f"PED-{D}-901", f"PED-{D}-902"]})
e = ruta_evento(inicio)
check(set(e) == CAMPOS_RUTA, f"crear: un evento con los campos exactos {sorted(e)}")
with app.app_context():
    ruta_hoy = db.session.query(Ruta).filter_by(conductor_id=usuario("sofia@sgds.com").id, fecha=hoy()).one()
    codigo_hoy = ruta_hoy.codigo
check(e.get("accion") == "creada" and e.get("paradas") == 2 and e.get("ruta") == codigo_hoy
      and e.get("conductor") == "Sofia Ruiz" and e.get("correo_destino") == "sofia.ruta@ejemplo.com"
      and e.get("fecha") == hoy().strftime("%d/%m/%Y") and e.get("origen") == "voz"
      and e.get("realizado_por") == "Carlos Rueda", f"crear: creada, 2 paradas, al conductor, por voz {e}")
check(re.fullmatch(r"\d\d/\d\d/\d{4}", e.get("fecha", "")) and e.get("detalle_ruta", "").startswith("Se te asignó"),
      f"fecha DD/MM/AAAA y un detalle corto: {e.get('detalle_ruta')}")
inicio = len(envios)
confirma("gestor/rutas", G, {"operacion": "agregar-parada", "conductor": "Sofia Ruiz", "pedido": f"PED-{D}-903"})
e = ruta_evento(inicio)
check(e.get("accion") == "actualizada" and e.get("paradas") == 3
      and e.get("detalle_ruta") == "Se agregó la parada de Supermercado El Portal",
      f"agregar-parada: un solo evento, actualizada, 3 paradas {e}")
inicio = len(envios)
confirma("gestor/rutas", G, {"operacion": "quitar-parada", "ruta": codigo_hoy, "pedido": f"PED-{D}-903"})
e = ruta_evento(inicio)
check(e.get("accion") == "actualizada" and e.get("paradas") == 2 and e.get("detalle_ruta", "").startswith("Se quitó"),
      f"quitar-parada: actualizada, 2 paradas {e}")
inicio = len(envios)
confirma("gestor/rutas", G, {"operacion": "recalcular", "ruta": codigo_hoy})
e = ruta_evento(inicio)
check(e.get("accion") == "actualizada" and e.get("paradas") == 2 and "recalculó" in e.get("detalle_ruta", ""),
      f"recalcular: actualizada {e}")
inicio = len(envios)
confirma("gestor/rutas", G, {"operacion": "eliminar", "ruta": codigo_hoy})
e = ruta_evento(inicio)
check(e.get("accion") == "eliminada" and e.get("paradas") == 0 and e.get("ruta") == codigo_hoy
      and "vuelven a pendientes" in e.get("detalle_ruta", ""), f"eliminar: eliminada {e}")
check(all(u == URL_SGDS for u, ev in envios if ev.get("tipo") == "ruta_asignada"), "todos van a MAKE_WEBHOOK_URL")


print("\n== 7. ruta_asignada en pantalla, y nunca de una ruta pasada ==")
gestor = navegador("despachador@sgds.com", "Despacho123*")
with app.app_context():
    sofia_id = usuario("sofia@sgds.com").id
    ids = [p.id for p in db.session.query(Pedido).filter(Pedido.codigo.like(f"PED-{M}-90%"))]
inicio = len(envios)
gestor.post("/rutas/nueva", data={"fecha": (hoy() + timedelta(days=1)).isoformat(), "conductor_id": sofia_id,
                                  "vehiculo_id": 0, "estrategia": ruteo.ESTRATEGIA_DISTANCIA, "pedido_id": ids})
e = ruta_evento(inicio)
check(e.get("accion") == "creada" and e.get("paradas") == 3 and e.get("origen") == "pantalla"
      and e.get("fecha") == (hoy() + timedelta(days=1)).strftime("%d/%m/%Y"),
      f"crear en pantalla una ruta de mañana avisa al conductor {e}")
with app.app_context():
    ruta_m = db.session.query(Ruta).filter_by(conductor_id=sofia_id, fecha=hoy() + timedelta(days=1)).one()
    ruta_m_id, primero = ruta_m.id, ruta_m.pedidos[0].id
for que, url, esperado in (("recalcular", f"/rutas/{ruta_m_id}/recalcular", ("actualizada", 3)),
                           ("quitar una parada", f"/rutas/{ruta_m_id}/quitar/{primero}", ("actualizada", 2)),
                           ("eliminar", f"/rutas/{ruta_m_id}/eliminar", ("eliminada", 0))):
    inicio = len(envios)
    gestor.post(url, data={"estrategia": ruteo.ESTRATEGIA_DISTANCIA})
    e = ruta_evento(inicio)
    check((e.get("accion"), e.get("paradas")) == esperado, f"{que} en pantalla: {e}")

with app.test_request_context():
    from app.services.planificacion import crear_ruta, recalcular_ruta
    ayer = db.session.query(Pedido).filter_by(codigo=f"PED-{D}-990").one()
    inicio = len(envios)
    ruta_ayer, _ = crear_ruta({"fecha": hoy() - timedelta(days=1), "conductor_id": sofia_id,
                               "pedidos": [ayer.id], "estrategia": ruteo.ESTRATEGIA_DISTANCIA}, gestor_id)
    recalcular_ruta(ruta_ayer, usuario_id=gestor_id)
    db.session.commit()
check(not avisos("ruta_asignada", inicio), "una ruta de ayer no avisa al conductor (ni al crearla ni al cambiarla)")


print("\n== 8. Cobertura: el propio correo de avisos queda fuera de la voz ==")
from app.asistentes import cobertura
check("auth.correo_avisos" in cobertura.COBERTURA and "Perfil" in cobertura.COBERTURA["auth.correo_avisos"].excluida,
      "auth.correo_avisos es una exclusion aprobada, con su motivo")
check("correo_avisos" in json.dumps(
    __import__("app.controllers.asistente_api", fromlist=["FUNCIONES"]).FUNCIONES[("admin", "usuarios")].parametros),
      "admin/usuarios acepta correo_avisos")


print("\n" + "=" * 55)
print("RESULTADO:", "TODAS LAS PRUEBAS PASARON" if not fallos else f"{len(fallos)} FALLAS")
for f in fallos: print("   - " + f)
sys.exit(1 if fallos else 0)
