import pathlib
import re, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from run import app

fallos = []
def check(cond, msg):
    print(("  OK   " if cond else "  FALLA") + f" {msg}")
    if not cond: fallos.append(msg)

app.config["WTF_CSRF_ENABLED"] = False

print("\n== 1. Acceso sin autenticar ==")
with app.test_client() as c:
    r = c.get("/", follow_redirects=True)
    check(b"Iniciar sesion" in r.data or b"Ingresar" in r.data, "redirige al login")
    r = c.get("/admin/", follow_redirects=False)
    check(r.status_code == 302, f"/admin protegido (status {r.status_code})")
    r = c.get("/salud")
    check(r.get_json() == {"estado": "ok"}, "endpoint /salud responde")

print("\n== 2. Credenciales invalidas (RNF5) ==")
with app.test_client() as c:
    r = c.post("/auth/login", data={"correo":"admin@sgds.com","contrasena":"incorrecta"}, follow_redirects=True)
    check(b"Credenciales invalidas" in r.data, "rechaza contrasena incorrecta")

print("\n== 3. Login administrador -> Tablero (RF1 + RF6) ==")
with app.test_client() as c:
    r = c.post("/auth/login", data={"correo":"admin@sgds.com","contrasena":"Admin123*"}, follow_redirects=True)
    check(r.status_code == 200, "login exitoso")
    check(b"Tablero de control" in r.data, "aterriza en el tablero")
    html = r.data.decode()
    kpis = re.findall(r'kpi-valor">([^<]+)<', html)
    print(f"       KPIs renderizados: {kpis}")
    check(len(kpis) == 3, "muestra los 3 KPIs del RF6")
    check("RUT-" in html, "lista las rutas del dia")
    check("SKU-1004" in html, "alerta de producto bajo minimo")
    r = c.get("/conductor/")
    check(r.status_code == 403, f"admin bloqueado en vista de conductor (status {r.status_code})")

print("\n== 4. Login conductor -> Mi ruta (RF4 + RNF1) ==")
with app.test_client() as c:
    r = c.post("/auth/login", data={"correo":"conductor1@sgds.com","contrasena":"Conductor123*"}, follow_redirects=True)
    check(b"Mi ruta" in r.data, "aterriza en su ruta del dia")
    html = r.data.decode()
    check("Supermercado El Portal" in html, "lista las paradas asignadas")
    n = html.count('class="parada ')
    check(n == 5, f"muestra 5 paradas (encontradas: {n})")
    r = c.get("/admin/")
    check(r.status_code == 403, f"conductor bloqueado en tablero admin (status {r.status_code})")

print("\n== 5. Logout ==")
with app.test_client() as c:
    c.post("/auth/login", data={"correo":"admin@sgds.com","contrasena":"Admin123*"})
    c.get("/auth/logout")
    r = c.get("/admin/", follow_redirects=False)
    check(r.status_code == 302, "sesion cerrada correctamente")

print("\n" + ("="*50))
print("RESULTADO: " + ("TODAS LAS PRUEBAS PASARON" if not fallos else f"{len(fallos)} FALLAS: {fallos}"))
sys.exit(1 if fallos else 0)
