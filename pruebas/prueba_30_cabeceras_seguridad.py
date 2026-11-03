"""RNF5 - Cabeceras de seguridad en las respuestas (hallazgos de ZAP/Nikto, 03/11/2026).

Verifica que cada respuesta lleve Content-Security-Policy con frame-ancestors,
X-Content-Type-Options, X-Frame-Options, Referrer-Policy y Permissions-Policy, que
no se revele la version del servidor, que la cookie de sesion sea HttpOnly y
SameSite, y que la pagina de restablecer conserve su Referrer-Policy "no-referrer".

Corre sin internet.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _preparar  # noqa: E402  Primero: base de pruebas, sin Make, Retell ni red externa.

from run import app
from app.extensions import db
from app.models import Usuario
from app.services import recuperacion

_preparar.reiniciar_base()

fallos = []


def check(c, m):
    print(("  OK   " if c else "  FALLA") + f" {m}")
    if not c:
        fallos.append(m)


cliente = app.test_client()

print("\n== 1. Cabeceras de seguridad en una pagina publica ==")
r = cliente.get("/auth/login")
csp = r.headers.get("Content-Security-Policy", "")
check("default-src 'self'" in csp, "envia Content-Security-Policy")
check("frame-ancestors 'self'" in csp, "la CSP incluye frame-ancestors (anti-clickjacking)")
check("object-src 'none'" in csp, "la CSP bloquea objetos embebidos")
check(r.headers.get("X-Content-Type-Options") == "nosniff", "X-Content-Type-Options: nosniff")
check(r.headers.get("X-Frame-Options") == "DENY", "X-Frame-Options: DENY")
check(r.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin", "Referrer-Policy por defecto")
check("microphone=(self)" in r.headers.get("Permissions-Policy", ""), "Permissions-Policy declara permisos")
check("Werkzeug" not in r.headers.get("Server", "") and "Python" not in r.headers.get("Server", ""),
      "la cabecera Server no revela Werkzeug/Python")

print("\n== 2. La CSP permite los origenes que la aplicacion necesita ==")
for origen in ("https://cdn.jsdelivr.net", "https://unpkg.com", "tile.openstreetmap.org"):
    check(origen in csp, f"la CSP permite {origen}")

print("\n== 3. Cookie de sesion endurecida ==")
with app.test_client() as c:
    html = c.get("/auth/login").data.decode()
    import re
    tok = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html).group(1)
    r = c.post("/auth/login", data={"csrf_token": tok, "correo": "admin@sgds.com", "contrasena": "Admin123*"})
    cookie = r.headers.get("Set-Cookie", "")
check("HttpOnly" in cookie, "la cookie de sesion es HttpOnly")
check("SameSite=Lax" in cookie, "la cookie de sesion lleva SameSite=Lax")

print("\n== 4. La pagina de restablecer no filtra el token por Referer ==")
with app.app_context():
    usuario = db.session.query(Usuario).filter_by(correo="cliente@sgds.com").one()
    token = recuperacion.generar_token(usuario)
r = cliente.get(f"/auth/restablecer/{token}")
check(r.headers.get("Referrer-Policy") == "no-referrer",
      "restablecer mantiene Referrer-Policy: no-referrer (mas estricta que la global)")

print("\n== 5. HSTS solo sobre HTTPS ==")
r = cliente.get("/auth/login")  # peticion HTTP de prueba
check("Strict-Transport-Security" not in r.headers, "sin HTTPS no se envia HSTS")
r = cliente.get("/auth/login", base_url="https://localhost")
check("max-age=" in r.headers.get("Strict-Transport-Security", ""), "sobre HTTPS se envia HSTS")

print("\n" + "=" * 55)
print("RESULTADO: " + ("TODAS LAS PRUEBAS PASARON" if not fallos else f"{len(fallos)} FALLAS"))
for f in fallos:
    print("   - " + f)
sys.exit(1 if fallos else 0)
