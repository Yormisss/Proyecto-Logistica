"""Resumen diario enviado por la app al escenario privado de Make (evento resumen_diario).

- El evento lleva exactamente los campos de GET /api/automatizacion/resumen-diario,
  en la raiz, mas `tipo`, y va solo a MAKE_WEBHOOK_RESUMEN_URL con x-make-apikey.
- Sin MAKE_WEBHOOK_RESUMEN_URL o sin CORREO_OPERACIONES no sale, y se dice por que.
- Quien lo origino: quien lo pidio (pantalla o voz) o el sistema (el comando).
- Boton "Enviar resumen del dia" del tablero: permiso propio, CSRF y mensaje.
- Voz: gestor/resumen-diario:enviar con confirmacion en el servidor.
- Comando `flask --app run enviar-resumen` y migracion m014.

Corre sin internet: Make y Retell se simulan.
"""

import json
import pathlib
import re
import sys
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _preparar  # noqa: E402  Primero: base de pruebas, sin Make, Retell ni red externa.
from retell.lib.webhook_auth import symmetric
from sqlalchemy import text

from run import app
from app.extensions import db
from app.models import Auditoria, RolAcceso, Rol, SesionAsistente, Usuario, VIGENCIA_SESION
from app.services import notificaciones
from app.services.asistente import huella_sesion
from app.services.notificaciones import NOMBRE_SISTEMA
from app.tiempo import ahora

fallos = []
def check(c, m):
    print(("  OK   " if c else "  FALLA") + f" {m}")
    if not c: fallos.append(m)

_preparar.reiniciar_base()

CLAVE = "clave-retell-resumen"
TOKEN = "token-respaldo-resumen"
OPERACIONES = "operaciones@sgds.invalid"
URL_SGDS = "https://hook.make.invalid/sgds"
URL_PRIVADO = "https://hook.make.invalid/privado"
API_KEY = "clave-make-prueba"
CONFIG = dict(RETELL_API_KEY=CLAVE, MAKE_WEBHOOK_URL=URL_SGDS, MAKE_WEBHOOK_KEY=API_KEY,
              MAKE_WEBHOOK_RESUMEN_URL=URL_PRIVADO, CORREO_OPERACIONES=OPERACIONES,
              AUTOMATIZACION_TOKEN=TOKEN, WTF_CSRF_ENABLED=False)
app.config.update(CONFIG)

envios = []
notificaciones.requests.post = lambda url, json=None, timeout=None, headers=None: (
    envios.append((url, headers or {}, json)), SimpleNamespace(raise_for_status=lambda: None))[1]

def resumenes(desde=0):
    notificaciones.esperar_envios()
    return [(url, cab, e) for url, cab, e in envios[desde:] if e.get("tipo") == "resumen_diario"]

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

def llamada(correo, call_id):
    with app.app_context():
        u = db.session.query(Usuario).filter_by(correo=correo).one()
        db.session.add(SesionAsistente(call_id=call_id, usuario_id=u.id, rol=u.rol, huella_sesion=huella_sesion(u),
                                       vence_en=ahora() + VIGENCIA_SESION))
        db.session.commit()

def comando():
    return app.test_cli_runner().invoke(args=["enviar-resumen"])

GESTOR, ADMIN = ("despachador@sgds.com", "Despacho123*"), ("admin@sgds.com", "Admin123*")


print("\n== 1. El evento lleva exactamente lo mismo que el GET de respaldo ==")
DESTINOS = ["admin@sgds.com", "despachador@sgds.com"]   # administradores y gestores activos
r = comando()
check(r.exit_code == 0 and f"enviado a Make para {', '.join(DESTINOS)}" in r.output,
      f"el comando lo envia: {r.output.strip()}")
eventos = resumenes()
check(sorted(e["correo_destino"] for _, _, e in eventos) == DESTINOS,
      "sale un resumen_diario por cada gestor y administrador activo, con su correo en correo_destino")
url, cabeceras, evento = eventos[0] if eventos else (None, {}, {})
check(all(u == URL_PRIVADO and c.get("x-make-apikey") == API_KEY for u, c, _ in eventos),
      "van a MAKE_WEBHOOK_RESUMEN_URL, con la misma API key en x-make-apikey")
check(not any(e.get("tipo") == "resumen_diario" for u, _, e in envios if u == URL_SGDS),
      "nunca al webhook de SGDS")
get = app.test_client().get("/api/automatizacion/resumen-diario", headers={"X-Automatizacion-Token": TOKEN})
respaldo = get.get_json() or {}
check(get.status_code == 200, "el GET de respaldo sigue respondiendo")
check(set(evento) == set(respaldo) | {"tipo"}, f"mismos campos que el GET, en la raiz, mas tipo {sorted(evento)}")
iguales = {k: evento.get(k) == respaldo.get(k) for k in respaldo if k not in ("fecha_hora", "correo_destino")}
check(all(iguales.values()),
      f"y con los mismos valores (salvo la hora del envio y el destinatario) {[k for k, v in iguales.items() if not v]}")
check(respaldo.get("correo_destino") == OPERACIONES, "el GET de respaldo sigue con CORREO_OPERACIONES")
check(isinstance(evento.get("kpis"), dict) and {"total", "entregados", "fallidos", "tasa_exito",
      "cumplimiento_ventana"} <= set(evento["kpis"]) and "<" in evento.get("resumen_html", ""),
      "con kpis anidado y el HTML del correo")
check(evento.get("realizado_por") == NOMBRE_SISTEMA and evento.get("origen") == "automático",
      "el comando lo origina el sistema (automatico)")
check(all(f.get("realizado_por") == NOMBRE_SISTEMA for f in evento.get("fallidos_para_reprogramar", [])),
      "y cada fallido para reprogramar lleva el mismo actor")
with app.app_context():
    registro = db.session.query(Auditoria).filter_by(accion="Enviar resumen del día").one()
    check(registro.usuario_id is None and registro.origen == "automático"
          and all(c in registro.resumen for c in DESTINOS),
          "el envio del comando queda en la auditoria, como del sistema, con sus destinatarios")
check(not [e for _, _, e in envios if e.get("tipo") == "accion_asistente"],
      "sin el correo accion_asistente: el resumen ya es un correo")


print("\n== 2. Sin webhook o sin destinatarios no sale; CORREO_OPERACIONES es el respaldo ==")
inicio = len(envios)
app.config["MAKE_WEBHOOK_RESUMEN_URL"] = ""
r = comando()
check(r.exit_code == 1 and "MAKE_WEBHOOK_RESUMEN_URL" in r.output, f"el comando termina con error y dice por que: {r.output.strip()}")
gestor = navegador(*GESTOR)
r = gestor.post("/admin/resumen/enviar", follow_redirects=True)
check("MAKE_WEBHOOK_RESUMEN_URL" in alertas(r), f"el boton lo dice en pantalla: {alertas(r)}")
llamada(GESTOR[0], "call_resumen_gestor")
m = dice("gestor/resumen-diario", "call_resumen_gestor", {"operacion": "enviar"})
check("MAKE_WEBHOOK_RESUMEN_URL" in m and not m.endswith("¿confirmas?"), f"y Gaby, sin pedir confirmacion: {m}")
app.config.update(MAKE_WEBHOOK_RESUMEN_URL=URL_PRIVADO, CORREO_OPERACIONES="")
r = comando()
check(r.exit_code == 0 and len(resumenes(inicio)) == 2,
      "sin CORREO_OPERACIONES sale igual a los gestores y administradores: es solo el respaldo")
# Sin ningun gestor ni administrador activo, el respaldo; y sin respaldo, nada.
with app.app_context():
    db.session.execute(text("UPDATE usuarios SET activo = 0 WHERE rol IN ('ADMIN', 'DESPACHADOR')"))
    db.session.commit()
inicio = len(envios)
r = comando()
check(r.exit_code == 1 and "CORREO_OPERACIONES" in r.output and not resumenes(inicio),
      f"sin destinatarios ni respaldo no sale, y el comando dice por que: {r.output.strip()}")
app.config.update(CORREO_OPERACIONES=OPERACIONES)
r = comando()
check(r.exit_code == 0 and [e["correo_destino"] for _, _, e in resumenes(inicio)] == [OPERACIONES],
      "sin gestores ni administradores activos, va al respaldo CORREO_OPERACIONES")
with app.app_context():
    db.session.execute(text("UPDATE usuarios SET activo = 1 WHERE rol IN ('ADMIN', 'DESPACHADOR')"))
    db.session.commit()


print("\n== 3. Boton del tablero: permiso propio, CSRF y mensaje ==")
admin = navegador(*ADMIN)
for quien, cliente in (("el gestor", gestor), ("el administrador", admin)):
    html = cliente.get("/admin/").get_data(as_text=True)
    check("Enviar resumen del día" in html and "/admin/resumen/enviar" in html,
          f"{quien} ve el boton en el tablero")
inicio = len(envios)
r = gestor.post("/admin/resumen/enviar", follow_redirects=True)
check(f"Resumen del día enviado a {', '.join(DESTINOS)}." in alertas(r), f"mensaje de resultado: {alertas(r)}")
eventos = resumenes(inicio)
check(len(eventos) == 2 and all(e["realizado_por"] == "Carlos Rueda" and e["origen"] == "pantalla"
                                and e["rol_realizado_por"] == "Gestor logístico" for _, _, e in eventos),
      "desde la pantalla lo origina quien lo pidio, en cada evento")
with app.app_context():
    registro = db.session.query(Auditoria).filter_by(accion="Enviar resumen del día", origen="pantalla").one()
check(registro.usuario_nombre == "Carlos Rueda", "y queda en la auditoria con su nombre")

app.config["WTF_CSRF_ENABLED"] = True
inicio = len(envios)
r = gestor.post("/admin/resumen/enviar")
check(r.status_code == 400 and not resumenes(inicio), "sin token CSRF se rechaza (400) y no se envia")
app.config["WTF_CSRF_ENABLED"] = False

with app.app_context():
    rol = db.session.query(RolAcceso).filter_by(clave_sistema=Rol.DESPACHADOR).one()
    check("resumen.enviar" in rol.permisos, "el rol del gestor trae el permiso en una base nueva")
    rol.asignaciones = [a for a in rol.asignaciones if a.permiso != "resumen.enviar"]
    db.session.commit()
inicio = len(envios)
html = gestor.get("/admin/").get_data(as_text=True)
check("Enviar resumen del día" not in html, "sin el permiso, el gestor no ve el boton")
check(gestor.post("/admin/resumen/enviar").status_code == 403 and not resumenes(inicio),
      "y un POST directo responde 403 sin enviar nada")
m = dice("gestor/resumen-diario", "call_resumen_gestor", {"operacion": "enviar"})
check("No tienes el permiso «Enviar el resumen del día»" in m, f"tampoco por voz: {m}")
from migraciones.m014_permiso_resumen import aplicar
with app.app_context():
    check(aplicar(verboso=False) is True, "m014 devuelve el permiso al rol del gestor")
    check(aplicar(verboso=False) is False, "y volver a ejecutarla no cambia nada")
check("Enviar resumen del día" in gestor.get("/admin/").get_data(as_text=True), "con el permiso, vuelve el boton")


print("\n== 4. Por voz, con confirmacion en el servidor ==")
llamada(ADMIN[0], "call_resumen_admin")
for correo, call_id, nombre, rol in ((GESTOR[0], "call_resumen_gestor", "Carlos Rueda", "Gestor logístico"),
                                     (ADMIN[0], "call_resumen_admin", "Laura Gomez", "Administrador")):
    inicio = len(envios)
    resumen = dice("gestor/resumen-diario", call_id, {"operacion": "enviar"})
    check(resumen == f"Voy a enviar el resumen de hoy a {' y '.join(DESTINOS)}. ¿confirmas?",
          f"{rol}: el resumen: {resumen}")
    check(not resumenes(inicio), f"{rol}: sin confirmar no se envia")
    final = dice("gestor/resumen-diario", call_id, {"operacion": "enviar", "confirmar": True})
    eventos = resumenes(inicio)
    check(final == f"Listo, envié el resumen de hoy a {' y '.join(DESTINOS)}." and len(eventos) == 2
          and all(e["realizado_por"] == nombre and e["origen"] == "voz" for _, _, e in eventos),
          f"{rol}: confirmado, se envia y lo origina quien lo pidio por voz")
with app.app_context():
    por_voz = db.session.query(Auditoria).filter_by(accion="Enviar resumen del día", origen="voz").all()
check(len(por_voz) == 2 and {r.call_id for r in por_voz} == {"call_resumen_gestor", "call_resumen_admin"},
      "cada envio por voz queda en la auditoria con su llamada")
check(not [e for _, _, e in envios if e.get("tipo") == "accion_asistente" and "resumen" in e.get("accion", "").lower()],
      "sin el correo accion_asistente")
inicio = len(envios)
check(dice("gestor/resumen-diario", "call_resumen_gestor", {"operacion": "enviar", "confirmar": True}).endswith("¿confirmas?")
      and not resumenes(inicio), "confirmar sin un resumen pendiente no envia: vuelve a resumir")


print("\n" + "=" * 55)
print("RESULTADO:", "TODAS LAS PRUEBAS PASARON" if not fallos else f"{len(fallos)} FALLAS")
for f in fallos: print("   - " + f)
sys.exit(1 if fallos else 0)
