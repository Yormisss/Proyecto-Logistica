import pathlib
import io, re, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from run import app
from app.extensions import db
from app.models import Pedido, Producto, PedidoItem, MovimientoInventario

fallos = []
def check(cond, msg):
    print(("  OK   " if cond else "  FALLA") + f" {msg}")
    if not cond: fallos.append(msg)

app.config["WTF_CSRF_ENABLED"] = False

def sesion_admin():
    c = app.test_client()
    c.post("/auth/login", data={"correo":"despachador@sgds.com","contrasena":"Despacho123*"})
    return c

print("\n== 1. Listado y filtros ==")
c = sesion_admin()
r = c.get("/pedidos/")
check(r.status_code == 200, "listado carga")
check(b"PED-" in r.data, "muestra pedidos existentes")
r = c.get("/pedidos/?estado=ENTREGADO")
html = r.data.decode()
check("Entregado" in html and "Fallido" not in html.split("tbody")[1], "filtra por estado")
r = c.get("/pedidos/?q=Chapinero")
check(b"Minimercado Chapinero" in r.data, "busca por texto")
r = c.get("/pedidos/?fecha=no-es-fecha")
check(b"fecha del filtro no es valida" in r.data, "rechaza fecha invalida sin romperse")

print("\n== 2. Plantilla CSV ==")
r = c.get("/pedidos/plantilla.csv")
check(r.status_code == 200 and "attachment" in r.headers.get("Content-Disposition",""), "plantilla descargable")
check(b"cliente_nombre" in r.data and b"SKU-1001" in r.data, "plantilla trae encabezados y ejemplo")

print("\n== 3. Alta manual ==")
with app.app_context():
    prod = db.session.query(Producto).filter_by(sku="SKU-1001").first()
    prod_id, stock_previo = prod.id, prod.stock_actual
    antes = db.session.query(Pedido).count()

r = c.post("/pedidos/nuevo", data={
    "cliente_nombre":"Tienda Prueba Manual", "direccion":"Calle 100 #15-20",
    "ciudad":"Bogota", "fecha_despacho":"2026-09-10", "prioridad":"1",
    "ventana_inicio":"08:00","ventana_fin":"12:00",
    "latitud":"4.6860","longitud":"-74.0480",
    "producto_id":[str(prod_id)], "cantidad":["7"],
}, follow_redirects=True)
check(b"registrado correctamente" in r.data, "crea pedido manual")
check(b"Tienda Prueba Manual" in r.data, "muestra el detalle del pedido creado")
with app.app_context():
    p = db.session.query(Pedido).filter_by(cliente_nombre="Tienda Prueba Manual").first()
    check(p is not None and p.codigo.startswith("PED-20260910-"), f"codigo autogenerado: {p.codigo if p else None}")
    check(len(p.items) == 1 and p.items[0].cantidad == 7, "guarda los items")
    check(p.estado == "PENDIENTE", "nace en estado PENDIENTE")
    check(len(p.eventos) == 1, "registra evento de trazabilidad")
    check(db.session.get(Producto, prod_id).stock_actual == stock_previo, "NO descuenta inventario al crear (RF5)")

print("\n== 4. Validaciones del alta manual ==")
r = c.post("/pedidos/nuevo", data={"cliente_nombre":"","direccion":"","fecha_despacho":"2026-09-10","prioridad":"3"}, follow_redirects=True)
check(b"Indique el cliente" in r.data, "exige cliente")
r = c.post("/pedidos/nuevo", data={
    "cliente_nombre":"X","direccion":"Y","fecha_despacho":"2026-09-10","prioridad":"3",
    "ventana_inicio":"14:00","ventana_fin":"09:00","producto_id":[str(prod_id)],"cantidad":["1"]}, follow_redirects=True)
check(b"posterior a la inicial" in r.data, "rechaza ventana horaria invertida")
r = c.post("/pedidos/nuevo", data={
    "cliente_nombre":"X","direccion":"Y","fecha_despacho":"2026-09-10","prioridad":"3"}, follow_redirects=True)
check(b"al menos un producto" in r.data, "exige al menos un producto")

print("\n== 5. Importacion CSV: archivo valido ==")
csv_ok = b"""cliente_nombre,direccion,ciudad,latitud,longitud,fecha_despacho,ventana_inicio,ventana_fin,prioridad,sku,cantidad,codigo
Panaderia Central,Cra 15 #80-10,Bogota,4.6680,-74.0550,2026-09-11,08:00,11:00,2,SKU-1001,4,LOTE-A
Panaderia Central,Cra 15 #80-10,Bogota,4.6680,-74.0550,2026-09-11,08:00,11:00,2,SKU-1002,6,LOTE-A
Cafeteria Norte,Calle 140 #12-05,Bogota,4.7220,-74.0330,2026-09-11,09:00,13:00,3,SKU-1003,3,LOTE-B
"""
r = c.post("/pedidos/importar", data={"archivo":(io.BytesIO(csv_ok),"pedidos.csv")},
           content_type="multipart/form-data", follow_redirects=True)
html = r.data.decode()
check("2 pedido(s) listos" in html, "agrupa 3 filas en 2 pedidos por codigo")
check("Panaderia Central" in html and "Cafeteria Norte" in html, "vista previa lista los pedidos")
token = re.search(r'name="token" value="([a-f0-9]{32})"', html)
check(token is not None, "genera token de confirmacion")

with app.app_context():
    antes = db.session.query(Pedido).count()
r = c.post("/pedidos/importar/confirmar", data={"token":token.group(1)}, follow_redirects=True)
check(b"2 pedido(s) cargados" in r.data, "confirma la carga")
with app.app_context():
    check(db.session.query(Pedido).count() == antes + 2, "persiste exactamente 2 pedidos")
    lote = db.session.query(Pedido).filter_by(codigo="LOTE-A").first()
    check(lote is not None and len(lote.items) == 2, "LOTE-A tiene 2 items agrupados")
    check(lote.ventana_inicio.strftime("%H:%M") == "08:00", "parsea la ventana horaria")
    check(lote.latitud == 4.6680, "parsea coordenadas")

print("\n== 6. Importacion CSV: errores por fila ==")
csv_malo = b"""cliente_nombre,direccion,sku,cantidad,fecha_despacho
Tienda Buena,Calle 1,SKU-1001,5,2026-09-12
,Calle 2,SKU-1001,5,2026-09-12
Tienda Sin SKU,Calle 3,SKU-9999,5,2026-09-12
Tienda Cant Cero,Calle 4,SKU-1001,0,2026-09-12
Tienda Fecha Mala,Calle 5,SKU-1001,5,32/13/2026
Tienda Dup,Calle 6,SKU-1001,5,2026-09-12
"""
r = c.post("/pedidos/importar", data={"archivo":(io.BytesIO(csv_malo),"malo.csv")},
           content_type="multipart/form-data", follow_redirects=True)
html = r.data.decode()
check("4 fila(s) con errores" in html, "detecta las 4 filas invalidas")
check("cliente_nombre es obligatorio" in html, "reporta cliente faltante")
check("SKU-9999" in html and "no existe en el inventario" in html, "reporta SKU inexistente")
check("mayor que cero" in html, "reporta cantidad cero")
check("2 pedido(s) listos" in html, "conserva las 2 filas validas")

print("\n== 7. Importacion CSV: archivos rechazados ==")
r = c.post("/pedidos/importar", data={"archivo":(io.BytesIO(b"a,b\n1,2\n"),"x.csv")},
           content_type="multipart/form-data", follow_redirects=True)
check(b"Faltan columnas obligatorias" in r.data, "rechaza encabezado incompleto")
r = c.post("/pedidos/importar", data={"archivo":(io.BytesIO(b"x"),"x.txt")},
           content_type="multipart/form-data", follow_redirects=True)
check(b"extension .csv" in r.data, "rechaza extension incorrecta")
r = c.post("/pedidos/importar/confirmar", data={"token":"0"*32}, follow_redirects=True)
check(b"vista previa expiro" in r.data, "token inexistente no crea nada")

print("\n== 8. Separador punto y coma (Excel es-CO) ==")
csv_pc = "cliente_nombre;direccion;sku;cantidad\nTienda Excel;Av 68 #10;SKU-1001;3\n".encode("latin-1")
r = c.post("/pedidos/importar", data={"archivo":(io.BytesIO(csv_pc),"excel.csv")},
           content_type="multipart/form-data", follow_redirects=True)
check(b"1 pedido(s) listos" in r.data, "detecta separador ;")

print("\n== 9. Inventario ==")
r = c.get("/inventario/")
check(b"SKU-1001" in r.data, "listado de productos")
r = c.get("/inventario/?criticos=1")
check(b"SKU-1004" in r.data and b"SKU-1001" not in r.data, "filtra bajo minimo")
r = c.post("/inventario/nuevo", data={"sku":"sku-2001","nombre":"Producto Nuevo","unidad":"UND",
           "stock_actual":"50","stock_minimo":"10","activo":"y"}, follow_redirects=True)
check(b"SKU-2001 creado" in r.data, "crea producto (SKU normalizado a mayusculas)")
with app.app_context():
    nuevo = db.session.query(Producto).filter_by(sku="SKU-2001").first()
    check(nuevo.stock_actual == 50, f"stock inicial correcto sin doble conteo (={nuevo.stock_actual})")
    movs = db.session.query(MovimientoInventario).filter_by(producto_id=nuevo.id).all()
    check(len(movs) == 1 and movs[0].stock_resultante == 50, "movimiento de stock inicial trazado")
    nid = nuevo.id
r = c.post("/inventario/nuevo", data={"sku":"SKU-2001","nombre":"Duplicado","stock_actual":"1","stock_minimo":"1"}, follow_redirects=True)
check(b"Ya existe un producto con este SKU" in r.data, "rechaza SKU duplicado")
r = c.post(f"/inventario/{nid}", data={"tipo":"ENTRADA","cantidad":"25","motivo":"Recepcion proveedor"}, follow_redirects=True)
check(b"Movimiento registrado" in r.data, "registra entrada")
with app.app_context():
    check(db.session.get(Producto, nid).stock_actual == 75, "entrada suma al stock")
r = c.post(f"/inventario/{nid}", data={"tipo":"AJUSTE","cantidad":"60","motivo":"Conteo fisico"}, follow_redirects=True)
with app.app_context():
    check(db.session.get(Producto, nid).stock_actual == 60, "ajuste reemplaza el stock")

print("\n== 10. Seguridad de los nuevos modulos ==")
cc = app.test_client()
cc.post("/auth/login", data={"correo":"conductor1@sgds.com","contrasena":"Conductor123*"})
for url in ["/pedidos/", "/pedidos/nuevo", "/pedidos/importar", "/inventario/", "/pedidos/plantilla.csv"]:
    check(cc.get(url).status_code == 403, f"conductor bloqueado en {url}")
anon = app.test_client()
check(anon.get("/pedidos/").status_code == 302, "anonimo redirigido al login")

print("\n== 11. Anulacion ==")
with app.app_context():
    entregado = db.session.query(Pedido).filter_by(estado="ENTREGADO").first()
    eid = entregado.id
    pendiente = db.session.query(Pedido).filter_by(cliente_nombre="Tienda Excel").first()
    pid = pendiente.id if pendiente else None
r = c.post(f"/pedidos/{eid}/anular", follow_redirects=True)
check(b"No se puede anular un pedido ya entregado" in r.data, "protege pedidos entregados")
if pid:
    r = c.post(f"/pedidos/{pid}/anular", follow_redirects=True)
    check(b"anulado" in r.data, "anula pedido pendiente")
    with app.app_context():
        check(db.session.get(Pedido, pid) is None, "elimina el pedido y sus items en cascada")

print("\n" + "="*55)
print("RESULTADO: " + ("TODAS LAS PRUEBAS PASARON" if not fallos else f"{len(fallos)} FALLAS"))
for f in fallos: print("   - " + f)
sys.exit(1 if fallos else 0)
