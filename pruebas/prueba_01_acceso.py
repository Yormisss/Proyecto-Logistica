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

print("\n== 6. Proteccion contra redireccion abierta (parametro next) ==")
with app.test_client() as c:
    r = c.post("/auth/login?next=/pedidos/",
               data={"correo":"admin@sgds.com","contrasena":"Admin123*"}, follow_redirects=False)
    check(r.headers.get("Location", "").endswith("/pedidos/"), "respeta un next relativo valido")

DESTINOS_MALICIOSOS = (
    "//evil.com",              # protocolo relativo: el navegador cambia de host
    "//evil.com/robar-sesion",
    "/\\evil.com",             # varios navegadores normalizan "\" a "/"
    "/\\/evil.com",
    "https://evil.com",        # esquema y dominio explicitos
    "http://evil.com/x",
    "javascript://evil.com",
)
for destino in DESTINOS_MALICIOSOS:
    with app.test_client() as c:
        r = c.post(f"/auth/login?next={destino}",
                   data={"correo":"admin@sgds.com","contrasena":"Admin123*"}, follow_redirects=False)
        destino_final = r.headers.get("Location", "")
        check("evil.com" not in destino_final,
              f"rechaza next={destino!r} y aterriza en el tablero, no en {destino_final!r}")

print("\n== 7. instance_path explicito y portabilidad de las pruebas ==")
raiz_proyecto = pathlib.Path(__file__).resolve().parent.parent
check(
    pathlib.Path(app.instance_path) == raiz_proyecto / "instance",
    f"instance_path apunta a la carpeta del proyecto, no a la auto-detectada por Flask ({app.instance_path})",
)
check(
    pathlib.Path(app.instance_path).is_dir(),
    "crear_app() crea la carpeta instance/ si no existia (clon nuevo, .gitignore)",
)

sys.path.insert(0, str(raiz_proyecto / "pruebas"))
import _preparar
check(
    _preparar.PYTHON == sys.executable,
    "pruebas/_preparar.py usa sys.executable, no una ruta fija a .venv (portable entre SO)",
)

print("\n== 8. ConfiguracionProduccion: variables obligatorias y opciones del motor ==")
import os
from unittest.mock import patch

from config import ConfiguracionProduccion
from app import crear_app

check(
    ConfiguracionProduccion.SQLALCHEMY_ENGINE_OPTIONS == {"pool_pre_ping": True, "pool_recycle": 280},
    f"agrega pool_pre_ping y pool_recycle=280 al motor ({ConfiguracionProduccion.SQLALCHEMY_ENGINE_OPTIONS})",
)

base_sin_vars = {k: v for k, v in os.environ.items() if k not in ("SECRET_KEY", "DATABASE_URL")}

with patch.dict(os.environ, base_sin_vars, clear=True):
    mensaje = None
    try:
        ConfiguracionProduccion.validar()
    except RuntimeError as error:
        mensaje = str(error)
check(mensaje is not None, "produccion no arranca sin SECRET_KEY ni DATABASE_URL")
check(
    mensaje is not None and "SECRET_KEY" in mensaje and "DATABASE_URL" in mensaje,
    f"el mensaje nombra las variables que faltan ({mensaje})",
)

with patch.dict(os.environ, base_sin_vars, clear=True):
    fallo_crear_app = False
    try:
        crear_app("produccion")
    except RuntimeError:
        fallo_crear_app = True
check(fallo_crear_app, "crear_app('produccion') tambien falla sin las variables obligatorias")

solo_secret = dict(base_sin_vars, SECRET_KEY="clave-de-prueba-suficientemente-larga")
with patch.dict(os.environ, solo_secret, clear=True):
    mensaje2 = None
    try:
        ConfiguracionProduccion.validar()
    except RuntimeError as error:
        mensaje2 = str(error)
check(
    mensaje2 is not None and "DATABASE_URL" in mensaje2 and "SECRET_KEY" not in mensaje2,
    f"si solo falta DATABASE_URL, el mensaje no la confunde con SECRET_KEY ({mensaje2})",
)

con_vars = dict(solo_secret, DATABASE_URL="mysql+pymysql://usuario:clave@host/logistica")
with patch.dict(os.environ, con_vars, clear=True):
    error_inesperado = None
    try:
        ConfiguracionProduccion.validar()
    except RuntimeError as error:
        error_inesperado = str(error)
check(
    error_inesperado is None,
    f"arranca sin error cuando SECRET_KEY y DATABASE_URL estan definidas ({error_inesperado})",
)

print("\n== 9. seed.py: trazabilidad de los pedidos entregados de hoy ==")
from app.extensions import db
from app.models import EstadoPedido, MovimientoInventario, Pedido, Producto, TipoMovimiento
from app.tiempo import hoy

with app.app_context():
    entregados_hoy = (
        db.session.query(Pedido)
        .filter(Pedido.fecha_despacho == hoy(), Pedido.estado == EstadoPedido.ENTREGADO)
        .all()
    )
    check(len(entregados_hoy) > 0, f"hay pedidos de hoy sembrados como ENTREGADO ({len(entregados_hoy)})")
    for pedido in entregados_hoy:
        check(len(pedido.movimientos) > 0,
              f"{pedido.codigo}: tiene MovimientoInventario (no solo la bandera inventario_descontado)")
        check(pedido.prueba_entrega is not None,
              f"{pedido.codigo}: tiene PruebaEntrega (su detalle la puede mostrar)")

    print("\n== 10. seed.py: inventario final sin negativos y con trazabilidad ==")
    productos = {p.sku: p for p in db.session.query(Producto).all()}
    check(all(p.stock_actual >= 0 for p in productos.values()),
          f"ningun producto queda en negativo tras el historico "
          f"({ {sku: p.stock_actual for sku, p in productos.items()} })")
    bajo = sorted(sku for sku, p in productos.items() if p.stock_actual <= p.stock_minimo)
    check(bajo == ["SKU-1004", "SKU-1006"], f"solo el aceite y el papel quedan bajo su minimo ({bajo})")
    check(productos["SKU-1001"].stock_actual > productos["SKU-1001"].stock_minimo,
          "las cajas de bebidas quedan sobre su minimo, para demostrar la alerta")

    # Trazabilidad: la entrada inicial menos las salidas, en orden cronologico,
    # encadena cada stock_resultante y termina en el stock actual.
    for producto in productos.values():
        movimientos = (
            db.session.query(MovimientoInventario)
            .filter_by(producto_id=producto.id)
            .order_by(MovimientoInventario.registrado_en, MovimientoInventario.id)
            .all()
        )
        inicial = movimientos[0] if movimientos else None
        saldo, rotos = (inicial.cantidad if inicial else 0), 0
        for m in movimientos[1:]:
            saldo += m.cantidad if m.tipo == TipoMovimiento.ENTRADA else -m.cantidad
            rotos += m.stock_resultante != saldo or saldo < 0
        check(inicial is not None and inicial.tipo == TipoMovimiento.ENTRADA
              and inicial.stock_resultante == inicial.cantidad and not rotos
              and saldo == producto.stock_actual,
              f"{producto.sku}: entrada inicial de {inicial.cantidad if inicial else '?'} y "
              f"{len(movimientos) - 1} salidas que encadenan hasta {producto.stock_actual} ({rotos} rotos)")

print("\n" + ("="*50))
print("RESULTADO: " + ("TODAS LAS PRUEBAS PASARON" if not fallos else f"{len(fallos)} FALLAS: {fallos}"))
sys.exit(1 if fallos else 0)
