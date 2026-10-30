"""Olvide mi contrasena: enlace por correo para restablecerla (RF1 / RNF5).

Cubre la pantalla para pedir el enlace, que la respuesta no revele que
cuentas existen, el envio por su propio webhook con URL_PUBLICA (no con el
Host de la peticion), el limite de un enlace cada pocos minutos, el enlace de
un solo uso y con vencimiento, la politica de contrasenas, el cierre de
sesiones y del bloqueo, la consola en desarrollo y la pantalla sin canal.
"""

import logging
import pathlib
import re
import sys
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _preparar  # noqa: E402  Primero: base de pruebas, sin Make, Retell ni red externa.
_preparar.reiniciar_base()
from itsdangerous.timed import TimestampSigner

from run import app
from app.extensions import db
from app.models import Auditoria, IntentoAcceso, Usuario
from app.services import notificaciones, recuperacion

fallos = []
def check(cond, msg):
    print(("  OK   " if cond else "  FALLA") + f" {msg}")
    if not cond: fallos.append(msg)

app.config["WTF_CSRF_ENABLED"] = False

envios_make, urls_make = [], []
notificaciones.requests.post = lambda url, json=None, timeout=None, headers=None: (
    envios_make.append(json), urls_make.append(url), SimpleNamespace(raise_for_status=lambda: None))[2]

WEBHOOK = "https://hook.make.test/restablecimiento"
URL_PUBLICA = "https://rutalytics.example.com"


def enlaces(desde):
    notificaciones.esperar_envios()
    return [e for e in envios_make[desde:] if e.get("tipo") == "restablecimiento_contrasena"]


def pedir(correo, cliente=None, **kwargs):
    cliente = cliente or app.test_client()
    return cliente.post("/auth/recuperar", data={"correo": correo}, follow_redirects=True, **kwargs)


def alertas(r):
    return " | ".join(re.findall(r'<div class="alerta alerta-\w+">(.*?)</div>', r.data.decode()))


def ruta_de(enlace):
    return urlsplit(enlace).path


def usuario(correo):
    with app.app_context():
        u = db.session.query(Usuario).filter_by(correo=correo).one()
        db.session.expunge(u)
        return u


def auditorias(accion):
    with app.app_context():
        return db.session.query(Auditoria).filter_by(accion=accion).all()


def entra(correo, clave):
    r = app.test_client().post("/auth/login", data={"correo": correo, "contrasena": clave})
    return r.status_code == 302


print("\n== 1. Pantalla para pedir el enlace ==")
app.config.update(MAKE_WEBHOOK_RESTABLECIMIENTO_URL=WEBHOOK, URL_PUBLICA=URL_PUBLICA)
html = app.test_client().get("/auth/login").data.decode()
check("¿Olvidó su contraseña?" in html and "/auth/recuperar" in html, "el login ofrece el enlace")
r = app.test_client().get("/auth/recuperar")
check(r.status_code == 200 and 'name="correo"' in r.data.decode(), "la pantalla pide el correo")
r = pedir("<script>@x.com")
check("Use solo letras" in r.data.decode(), "el correo pasa por la lista blanca")
admin = app.test_client()
admin.post("/auth/login", data={"correo": "admin@sgds.com", "contrasena": "Admin123*"})
r = admin.get("/auth/recuperar")
check(r.status_code == 302 and "/auth/perfil" in r.headers["Location"],
      "con la sesion iniciada lleva al perfil")

print("\n== 2. La respuesta no revela que cuentas existen ==")
inicio = len(envios_make)
r_real = pedir("conductor1@sgds.com")
r_falso = pedir("nadie.existe@sgds.com")
check(alertas(r_real) == alertas(r_falso) and "Si el correo pertenece" in alertas(r_real),
      "un correo real y uno inexistente reciben el mismo mensaje")
check("/auth/login" in r_real.request.path, "y ambos vuelven al login")
check(len(enlaces(inicio)) == 1, "solo la cuenta real recibe un enlace")

print("\n== 3. El enlace sale por su webhook, con URL_PUBLICA ==")
evento = enlaces(inicio)[0]
check(urls_make[inicio] == WEBHOOK, "va al webhook de restablecimiento, no al general")
check(evento["correo_destino"] == "conductor1@sgds.com" and evento["vence_en_minutos"] == 30,
      "al correo de la cuenta, con el vencimiento")
check(evento["enlace"].startswith(URL_PUBLICA + "/auth/restablecer/"), "armado con URL_PUBLICA")
check(evento["realizado_por"] == usuario("conductor1@sgds.com").nombre,
      "quien lo pidio figura como actor")
check(usuario("conductor1@sgds.com").verificar_contrasena("Conductor123*"),
      "pedir el enlace no cambia la contrasena")
solicitudes = auditorias(recuperacion.ACCION_SOLICITUD)
check(len(solicitudes) == 1 and evento["enlace"].rsplit("/", 1)[1] not in (solicitudes[0].detalle or "")
      + solicitudes[0].resumen, "la solicitud queda en la auditoria, sin el enlace")

inicio = len(envios_make)
pedir("despachador@sgds.com", base_url="http://atacante.example")
enlace_despachador = enlaces(inicio)[0]["enlace"]
check(enlace_despachador.startswith(URL_PUBLICA),
      "un Host falso en la peticion no cambia el dominio del enlace")

print("\n== 4. Un enlace por cuenta cada 5 minutos ==")
inicio = len(envios_make)
r = pedir("conductor1@sgds.com")
check("Si el correo pertenece" in alertas(r) and not enlaces(inicio),
      "un segundo pedido inmediato responde igual pero no envia otro")

print("\n== 5. Definir la contrasena con el enlace ==")
ruta = ruta_de(evento["enlace"])
c = app.test_client()
r = c.get(ruta)
check(r.status_code == 200 and 'name="contrasena"' in r.data.decode(), "el enlace abre el formulario")
check(r.headers.get("Referrer-Policy") == "no-referrer", "la pagina no envia el token en el Referer")
r = c.post(ruta, data={"contrasena": "123456789101112131415", "confirmacion": "123456789101112131415"})
check("al menos una mayúscula" in r.data.decode(), "aplica la politica de contrasenas")
r = c.post(ruta, data={"contrasena": "Recuperada123*", "confirmacion": "Otra123456*"})
check("no coinciden" in r.data.decode(), "exige la confirmacion")
r = c.post(ruta, data={"contrasena": "Conductor123*", "confirmacion": "Conductor123*"})
check("distinta de la actual" in alertas(r), "no acepta la misma contrasena")
check(usuario("conductor1@sgds.com").verificar_contrasena("Conductor123*"), "los intentos invalidos no la tocan")

abierta = app.test_client()
abierta.post("/auth/login", data={"correo": "conductor1@sgds.com", "contrasena": "Conductor123*"})
check(abierta.get("/conductor/").status_code == 200, "el conductor tiene una sesion abierta")
for _ in range(5):
    entra("conductor1@sgds.com", "Equivocada9*")
check(not entra("conductor1@sgds.com", "Conductor123*"), "y la cuenta queda bloqueada por intentos")

r = c.post(ruta, data={"contrasena": "Recuperada123*", "confirmacion": "Recuperada123*"},
           follow_redirects=True)
check("Contrasena actualizada" in alertas(r) and r.request.path == "/auth/login",
      "guarda la contrasena y lleva al login")
u = usuario("conductor1@sgds.com")
check(u.verificar_contrasena("Recuperada123*") and not u.debe_cambiar_contrasena,
      "la nueva queda vigente, sin cambio obligatorio")
check(abierta.get("/conductor/").status_code == 302, "las sesiones abiertas se cierran")
with app.app_context():
    check(db.session.get(IntentoAcceso, "conductor1@sgds.com") is None, "el bloqueo se levanta")
check(entra("conductor1@sgds.com", "Recuperada123*"), "entra con la nueva")
check(not entra("conductor1@sgds.com", "Conductor123*"), "la anterior ya no sirve")
check(len(auditorias(recuperacion.ACCION_RESTABLECIMIENTO)) == 1, "el cambio queda en la auditoria")

print("\n== 6. Enlaces que ya no sirven ==")
r = app.test_client().get(ruta)
check(r.status_code == 400 and "ya se usó" in r.data.decode(), "el mismo enlace no sirve dos veces")
r = app.test_client().post(ruta, data={"contrasena": "OtraVez123*", "confirmacion": "OtraVez123*"})
check(r.status_code == 400 and usuario("conductor1@sgds.com").verificar_contrasena("Recuperada123*"),
      "ni por POST")
check(app.test_client().get(ruta[:-3] + "abc").status_code == 400, "un token alterado no sirve")
check(app.test_client().get("/auth/restablecer/basura").status_code == 400, "uno inventado tampoco")

# El enlace del gestor deja de servir si su contrasena cambia por otra via.
with app.app_context():
    d = db.session.query(Usuario).filter_by(correo="despachador@sgds.com").one()
    d.establecer_contrasena("CambioAdmin123*", temporal=True)
    db.session.commit()
check(app.test_client().get(ruta_de(enlace_despachador)).status_code == 400,
      "un enlace pedido antes de otro cambio de contrasena caduca")

# Vencido: firmado hace 31 minutos.
original = TimestampSigner.get_timestamp
TimestampSigner.get_timestamp = lambda self: int(time.time()) - 31 * 60
with app.test_request_context():
    vencido = recuperacion.generar_token(db.session.query(Usuario).filter_by(correo="cliente@sgds.com").one())
TimestampSigner.get_timestamp = original
check(app.test_client().get(f"/auth/restablecer/{vencido}").status_code == 400,
      "un enlace de hace mas de 30 minutos vencio")

print("\n== 7. Cuentas inactivas ==")
with app.app_context():
    db.session.query(Usuario).filter_by(correo="conductor2@sgds.com").one().activo = False
    db.session.commit()
inicio = len(envios_make)
r = pedir("conductor2@sgds.com")
check("Si el correo pertenece" in alertas(r) and not enlaces(inicio),
      "una cuenta inactiva no recibe enlace, con el mismo mensaje")

print("\n== 8. Desarrollo sin webhook: el enlace sale en la consola ==")
app.config.update(MAKE_WEBHOOK_RESTABLECIMIENTO_URL="", URL_PUBLICA="")
capturados = []
manejador = logging.Handler()
manejador.emit = lambda registro: capturados.append(registro.getMessage())
app.logger.addHandler(manejador)
inicio = len(envios_make)
r = pedir("cliente@sgds.com")
app.logger.removeHandler(manejador)
check("Si el correo pertenece" in alertas(r) and not enlaces(inicio), "no envia nada a Make")
mensaje = next((m for m in capturados if "restablecer la contrasena de cliente@sgds.com" in m), "")
check("http://localhost/auth/restablecer/" in mensaje, "escribe el enlace en la consola")
ruta_cliente = "/auth/restablecer/" + mensaje.rsplit("/auth/restablecer/", 1)[1].strip()
r = app.test_client().post(ruta_cliente, data={"contrasena": "ClienteNueva1*",
                                               "confirmacion": "ClienteNueva1*"})
check(r.status_code == 302 and entra("cliente@sgds.com", "ClienteNueva1*"),
      "y ese enlace restablece la contrasena")

print("\n== 9. Produccion sin canal de correo ==")
app.debug = False
html = app.test_client().get("/auth/login").data.decode()
check("¿Olvidó su contraseña?" not in html, "el login no ofrece el enlace")
r = app.test_client().get("/auth/recuperar")
check("no está habilitada" in r.data.decode() and 'name="correo"' not in r.data.decode(),
      "la pantalla explica que lo pida al administrador")
app.config.update(MAKE_WEBHOOK_RESTABLECIMIENTO_URL=WEBHOOK)
check("¿Olvidó su contraseña?" not in app.test_client().get("/auth/login").data.decode(),
      "con el webhook pero sin URL_PUBLICA tampoco")
app.config.update(URL_PUBLICA=URL_PUBLICA)
check("¿Olvidó su contraseña?" in app.test_client().get("/auth/login").data.decode(),
      "con webhook y URL_PUBLICA si")
app.debug = True


print("\n" + "=" * 55)
print("RESULTADO: " + ("TODAS LAS PRUEBAS PASARON" if not fallos else f"{len(fallos)} FALLAS"))
for f in fallos: print("   - " + f)
sys.exit(1 if fallos else 0)
