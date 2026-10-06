"""Automatizaciones con Make: avisos con tipo y resumen diario.

Corre sin internet: el POST a Make se simula y se registran los avisos que
habrian salido, con sus encabezados.
"""

import pathlib
import re
import sys
from datetime import time, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from run import app
from app.controllers.inventario import registrar_movimiento
from app.extensions import db
from app.models import (Cliente, EstadoPedido, EstadoRuta, Pedido, PedidoItem, Producto,
                        PruebaEntrega, Ruta, TipoMovimiento, Usuario)
from app.services import notificaciones
from app.tiempo import ahora, hoy

fallos = []
def check(c, m):
    print(("  OK   " if c else "  FALLA") + f" {m}")
    if not c: fallos.append(m)

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _preparar import reiniciar_base
reiniciar_base()

URL_MAKE = "https://hook.make.invalid/sgds"
CLAVE_MAKE = "clave-make-prueba"
OPERACIONES = "operaciones@sgds.invalid"
TOKEN = "token-automatizacion-prueba"
RESUMEN = "/api/automatizacion/resumen-diario"

app.config["WTF_CSRF_ENABLED"] = True
# La configuracion se fija aqui para que el .env local no cambie el resultado.
app.config.update(RETELL_API_KEY="", MAKE_WEBHOOK_URL=URL_MAKE, MAKE_WEBHOOK_KEY=CLAVE_MAKE,
                  CORREO_OPERACIONES=OPERACIONES, AUTOMATIZACION_TOKEN="")


# ---- Make simulado ----
class RespuestaMake:
    def raise_for_status(self): pass

envios_make = []
def post_make(url, json=None, timeout=None, headers=None):
    envios_make.append({"url": url, "json": json, "timeout": timeout, "headers": headers})
    return RespuestaMake()
notificaciones.requests.post = post_make

def avisos(tipo, desde):
    """Avisos de `tipo` enviados desde la posicion `desde` de envios_make."""
    notificaciones.esperar_envios()
    return [e["json"] for e in envios_make[desde:] if e["json"].get("tipo") == tipo]


# ---- Utilidades ----
def sesion(correo, clave):
    c = app.test_client()
    html = c.get("/auth/login").data.decode()
    tok = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html).group(1)
    c.post("/auth/login", data={"csrf_token": tok, "correo": correo, "contrasena": clave})
    return c

def token_csrf(c, url):
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', c.get(url).data.decode())
    return m.group(1) if m else None

def ajustar(producto_id, cantidad):
    """Ajuste por inventario fisico desde la vista del producto."""
    url = f"/inventario/{producto_id}"
    return despachador.post(url, data={"csrf_token": token_csrf(despachador, url),
                                       "tipo": TipoMovimiento.AJUSTE, "cantidad": cantidad})

def entregar(pedido_id):
    url = f"/conductor/parada/{pedido_id}"
    return conductor2.post(f"{url}/entregar", data={"csrf_token": token_csrf(conductor2, url),
                                                     "receptor_nombre": "Recibe"})

CAMPOS_STOCK = {"tipo", "sku", "producto", "stock_actual", "stock_minimo", "negativo", "pedido",
                "correo_destino", "hora"}
CAMPOS_PEDIDO = {"tipo", "codigo", "estado", "cliente", "correo", "direccion", "ventana", "hora"}


# ---- Escenario ----
with app.app_context():
    usuario = lambda correo: db.session.query(Usuario).filter_by(correo=correo).one()
    c2_id, desp_id = usuario("conductor2@sgds.com").id, usuario("despachador@sgds.com").id
    cliente = lambda nombre: db.session.query(Cliente).filter_by(nombre=nombre).one()
    portal, esquina = cliente("Supermercado El Portal"), cliente("Tienda La Esquina")
    usaquen, fontibon = cliente("Tienda Usaquen"), cliente("Tienda Fontibon")
    # Correos fijos: no se depende de SEMILLA_CORREO_CLIENTE del .env local.
    portal.correo, esquina.correo = "compras@portal.invalid", "esquina@tienda.invalid"
    usaquen.correo, fontibon.correo = None, "   "
    portal_id, esquina_id, usaquen_id, fontibon_id = portal.id, esquina.id, usaquen.id, fontibon.id

    def producto(sku, stock, minimo, activo=True):
        p = Producto(sku=sku, nombre=f"Producto {sku}", stock_actual=stock, stock_minimo=minimo,
                     activo=activo)
        db.session.add(p); db.session.flush()
        return p.id
    prod_a = producto("SKU-AUTO-A", 20, 10)            # ajustes manuales
    prod_b = producto("SKU-AUTO-B", 6, 4)              # entregas: minimo y luego cero
    prod_c = producto("SKU-AUTO-C", 3, 2)              # una entrega cruza ambos
    prod_d = producto("SKU-AUTO-D", 20, 10, activo=False)
    prod_e = producto("SKU-AUTO-E", 20, 10)            # rollback y sin CORREO_OPERACIONES

    # Ruta de hoy de conductor2 con cinco paradas en camino.
    ruta = Ruta(codigo="RUT-AUTO-01", fecha=hoy(), estado=EstadoRuta.EN_CURSO, conductor_id=c2_id,
                iniciada_en=ahora())
    db.session.add(ruta); db.session.flush()
    entregas = {}
    for orden, (prod, cantidad) in enumerate(
            [(prod_b, 2), (prod_b, 1), (prod_b, 5), (prod_b, 1), (prod_c, 5)], start=1):
        p = Pedido(codigo=f"AUTO-{orden:03d}", cliente_id=esquina_id, cliente_nombre="Tienda La Esquina",
                   direccion="Calle 63 #24-18", ciudad="Bogota", fecha_despacho=hoy(),
                   estado=EstadoPedido.EN_RUTA, ruta_id=ruta.id, orden_en_ruta=orden,
                   creado_por_id=desp_id)
        db.session.add(p); db.session.flush()
        db.session.add(PedidoItem(pedido_id=p.id, producto_id=prod, cantidad=cantidad))
        entregas[orden] = p.id
    db.session.commit()

despachador = sesion("despachador@sgds.com", "Despacho123*")
conductor2 = sesion("conductor2@sgds.com", "Conductor123*")


print("\n== 1. stock_bajo al cruzar el minimo (ajuste manual) ==")
inicio = len(envios_make)
ajustar(prod_a, 15)
check(not avisos("stock_bajo", inicio), "20 -> 15 (minimo 10): sin aviso")

inicio = len(envios_make)
r = ajustar(prod_a, 10)
recibidos = avisos("stock_bajo", inicio)
check(r.status_code == 302 and len(recibidos) == 1, f"15 -> 10, en el minimo: un aviso ({len(recibidos)})")
if recibidos:
    aviso = recibidos[0]
    check(set(aviso) == CAMPOS_STOCK, f"con los campos del evento {sorted(aviso)}")
    check(aviso["sku"] == "SKU-AUTO-A" and aviso["producto"] == "Producto SKU-AUTO-A"
          and aviso["stock_actual"] == 10 and aviso["stock_minimo"] == 10,
          "sku, producto, stock_actual y stock_minimo")
    check(aviso["negativo"] is False, "negativo=false")
    check(aviso["pedido"] is None, "pedido=null en un movimiento manual")
    check(aviso["correo_destino"] == OPERACIONES, "correo_destino es CORREO_OPERACIONES")

inicio = len(envios_make)
ajustar(prod_a, 7)
check(not avisos("stock_bajo", inicio), "10 -> 7, ya bajo el minimo: no repite el aviso")
ajustar(prod_a, 30)
check(not avisos("stock_bajo", inicio), "7 -> 30, reposicion: sin aviso")
ajustar(prod_a, 9)
check(len(avisos("stock_bajo", inicio)) == 1, "30 -> 9: tras reponer, el nuevo cruce vuelve a avisar")

inicio = len(envios_make)
ajustar(prod_d, 5)
check(not avisos("stock_bajo", inicio), "un producto inactivo no genera aviso")

inicio = len(envios_make)
with app.app_context():
    registrar_movimiento(db.session.get(Producto, prod_e), TipoMovimiento.AJUSTE, 5, desp_id)
    db.session.rollback()
check(not avisos("stock_bajo", inicio), "un movimiento revertido (rollback) no envia nada")
with app.app_context():
    check(db.session.get(Producto, prod_e).stock_actual == 20, "y el stock queda intacto")


print("\n== 2. stock_bajo en entregas: minimo y cero ==")
inicio = len(envios_make)
r = entregar(entregas[1])
recibidos = avisos("stock_bajo", inicio)
check(r.status_code == 302 and len(recibidos) == 1, "SKU-AUTO-B 6 -> 4 (minimo 4): un aviso")
if recibidos:
    check(recibidos[0]["pedido"] == "AUTO-001" and recibidos[0]["negativo"] is False,
          "con el codigo del pedido entregado y negativo=false")
estado_entregado = avisos("pedido_estado", inicio)
check(len(estado_entregado) == 1 and estado_entregado[0]["estado"] == "ENTREGADO",
      "la misma entrega envia tambien su aviso pedido_estado")
check(estado_entregado and set(estado_entregado[0]) == CAMPOS_PEDIDO,
      "ENTREGADO conserva los campos de siempre, sin motivo")

inicio = len(envios_make)
entregar(entregas[2])
check(not avisos("stock_bajo", inicio), "4 -> 3, ya bajo el minimo: sin aviso")

inicio = len(envios_make)
entregar(entregas[3])
recibidos = avisos("stock_bajo", inicio)
check(len(recibidos) == 1, f"3 -> -2: cruza el cero, un aviso ({len(recibidos)})")
if recibidos:
    check(recibidos[0]["negativo"] is True and recibidos[0]["stock_actual"] == -2,
          "negativo=true con el stock negativo")
    check(recibidos[0]["pedido"] == "AUTO-003", "con el pedido que lo dejo negativo")

inicio = len(envios_make)
entregar(entregas[4])
check(not avisos("stock_bajo", inicio), "-2 -> -3, ya negativo: sin aviso")

inicio = len(envios_make)
entregar(entregas[5])
recibidos = avisos("stock_bajo", inicio)
check(len(recibidos) == 1, f"SKU-AUTO-C 3 -> -2 (minimo 2): cruza ambos umbrales, un solo aviso ({len(recibidos)})")
if recibidos:
    check(recibidos[0]["negativo"] is True and recibidos[0]["sku"] == "SKU-AUTO-C", "con negativo=true")


print("\n== 3. Vista del conductor con la ruta terminada ==")
with app.app_context():
    check(db.session.query(Ruta).filter_by(codigo="RUT-AUTO-01").one().estado == EstadoRuta.FINALIZADA,
          "con las cinco paradas entregadas la ruta queda FINALIZADA")
html = conductor2.get("/conductor/").data.decode()
check("Completaste tu ruta de hoy" in html and "No tiene una ruta asignada" not in html,
      "la vista del conductor muestra 'Completaste tu ruta de hoy'")


print("\n== 4. Encabezado x-make-apikey ==")
check(envios_make and all(e["headers"] == {"x-make-apikey": CLAVE_MAKE} for e in envios_make),
      "con MAKE_WEBHOOK_KEY todos los avisos llevan x-make-apikey")
check(all(e["url"] == URL_MAKE and e["timeout"] == (3, 5) for e in envios_make),
      "todos van a MAKE_WEBHOOK_URL con timeout corto")
app.config["MAKE_WEBHOOK_KEY"] = ""


print("\n== 5. Sin CORREO_OPERACIONES no hay avisos internos ==")
app.config["CORREO_OPERACIONES"] = ""
inicio = len(envios_make)
ajustar(prod_e, 5)
check(not avisos("stock_bajo", inicio), "20 -> 5 cruza el minimo, pero no se envia nada")


print("\n== 6. pedido_estado CANCELADO con motivo ==")
with app.app_context():
    def pedido_suelto(codigo, cliente_id, nombre):
        p = Pedido(codigo=codigo, cliente_id=cliente_id, cliente_nombre=nombre, direccion="Av. Cra 68 #75-50",
                   ciudad="Bogota", fecha_despacho=hoy(), estado=EstadoPedido.PENDIENTE,
                   creado_por_id=desp_id, ventana_inicio=time(8, 0), ventana_fin=time(12, 0))
        db.session.add(p); db.session.flush()
        return p.id
    cancelar_portal = pedido_suelto("AUTO-CAN-1", portal_id, "Supermercado El Portal")
    cancelar_usaquen = pedido_suelto("AUTO-CAN-2", usaquen_id, "Tienda Usaquen")
    db.session.commit()

def anular(pedido_id, motivo):
    url = f"/pedidos/{pedido_id}"
    return despachador.post(f"{url}/anular", data={"csrf_token": token_csrf(despachador, url),
                                                   "motivo": motivo})

inicio = len(envios_make)
r = anular(cancelar_portal, "El cliente cancelo la compra")
recibidos = avisos("pedido_estado", inicio)
check(r.status_code == 302 and len(recibidos) == 1, "anular un pedido de El Portal envia un aviso")
if recibidos:
    aviso = recibidos[0]
    check(aviso["estado"] == "CANCELADO" and aviso["motivo"] == "El cliente cancelo la compra",
          "estado CANCELADO con el motivo de la anulacion")
    check(set(aviso) == CAMPOS_PEDIDO | {"motivo"}, f"los campos de siempre mas motivo {sorted(aviso)}")
    check(aviso["correo"] == "compras@portal.invalid" and aviso["ventana"] == "08:00 - 12:00",
          "con el correo del cliente y la ventana")
check(envios_make[-1]["headers"] == {}, "sin MAKE_WEBHOOK_KEY no se envia x-make-apikey")
check(not app.config["CORREO_OPERACIONES"],
      "los avisos al cliente salen aunque CORREO_OPERACIONES este vacio")

inicio = len(envios_make)
anular(cancelar_usaquen, "Duplicado")
with app.app_context():
    check(db.session.get(Pedido, cancelar_usaquen).estado == EstadoPedido.CANCELADO, "AUTO-CAN-2 queda anulado")
check(not avisos("pedido_estado", inicio), "un cliente sin correo no recibe aviso de anulacion")


print("\n== 7. GET /api/automatizacion/resumen-diario: autenticacion ==")
cliente_make = app.test_client()
def resumen(token=None):
    cabeceras = {} if token is None else {"X-Automatizacion-Token": token}
    return cliente_make.get(RESUMEN, headers=cabeceras)

check(resumen(TOKEN).status_code == 404, "sin AUTOMATIZACION_TOKEN el endpoint no existe (404)")
check(resumen().status_code == 404, "tampoco sin encabezado")
app.config["AUTOMATIZACION_TOKEN"] = TOKEN
check(resumen().status_code == 401, "sin el encabezado: 401")
check(resumen("").status_code == 401, "con el encabezado vacio: 401")
check(resumen("token-incorrecto").status_code == 401, "con un token incorrecto: 401")
check(resumen(TOKEN + "x").status_code == 401, "con el token mas un caracter: 401")
check(resumen("contraseña").status_code == 401, "con un token no ASCII: 401 (no 500)")
r = resumen(TOKEN)
check(r.status_code == 200 and r.is_json, f"con el token correcto: 200 JSON ({r.status_code})")
check(cliente_make.post(RESUMEN, headers={"X-Automatizacion-Token": TOKEN}).status_code == 405,
      "solo acepta GET")


print("\n== 8. Contenido del resumen ==")
# Fallidos de hoy: con correo (2), sin correo, con correo en blanco y uno de ayer.
with app.app_context():
    def fallido(codigo, cliente_id, nombre, motivo, fecha=None):
        p = Pedido(codigo=codigo, cliente_id=cliente_id, cliente_nombre=nombre, direccion="Calle 80 #10-20",
                   ciudad="Bogota", fecha_despacho=fecha or hoy(), estado=EstadoPedido.FALLIDO,
                   creado_por_id=desp_id)
        db.session.add(p); db.session.flush()
        db.session.add(PruebaEntrega(pedido_id=p.id, motivo_fallo=motivo, registrado_en=ahora()))
    fallido("AUTO-FAL-1", portal_id, "Supermercado El Portal", "Cliente ausente")
    fallido("AUTO-FAL-2", portal_id, "Tienda <script>alert(1)</script>", "Direccion <b>incorrecta</b>")
    fallido("AUTO-FAL-3", usaquen_id, "Tienda Usaquen", "Establecimiento cerrado")
    fallido("AUTO-FAL-4", fontibon_id, "Tienda Fontibon", "Cliente ausente")
    fallido("AUTO-FAL-5", portal_id, "Supermercado El Portal", "Cliente ausente", hoy() - timedelta(days=1))
    db.session.commit()

app.config["CORREO_OPERACIONES"] = f"  {OPERACIONES} "
datos = resumen(TOKEN).get_json()
check(set(datos) == {"fecha", "correo_destino", "kpis", "productos_bajo_minimo",
                     "fallidos_para_reprogramar", "resumen_html"}, f"campos del resumen {sorted(datos)}")
check(datos["fecha"] == hoy().isoformat(), "fecha de hoy en formato ISO")
check(datos["correo_destino"] == OPERACIONES, "correo_destino es CORREO_OPERACIONES (sin espacios)")

# KPIs: contra lo que muestra el tablero, no contra la misma funcion.
tablero = despachador.get("/admin/").data.decode()
valores = re.findall(r'<span class="kpi-valor">([\d.]+)%?</span>', tablero)
detalle = re.search(r"(\d+) entregadas &middot; (\d+) fallidas", tablero)
kpis = datos["kpis"]
check(set(kpis) == {"total", "entregados", "fallidos", "cancelados", "pendientes", "tasa_exito",
                    "cumplimiento_ventana"}, f"campos de kpis {sorted(kpis)}")
check(len(valores) == 3 and detalle, "el tablero muestra sus tres KPIs")
if len(valores) == 3 and detalle:
    check(kpis["total"] == int(valores[0]), f"total coincide con el tablero ({kpis['total']} / {valores[0]})")
    check(kpis["pendientes"] == int(valores[1]), f"pendientes coincide ({kpis['pendientes']} / {valores[1]})")
    check(kpis["tasa_exito"] == float(valores[2]), f"tasa de exito coincide ({kpis['tasa_exito']} / {valores[2]})")
    check((kpis["entregados"], kpis["fallidos"]) == (int(detalle[1]), int(detalle[2])),
          f"entregados y fallidos coinciden ({kpis['entregados']}, {kpis['fallidos']})")

with app.app_context():
    de_hoy = db.session.query(Pedido).filter(Pedido.fecha_despacho == hoy()).all()
    cancelados = sum(1 for p in de_hoy if p.estado == EstadoPedido.CANCELADO)
    con_ventana = [p for p in de_hoy if p.estado == EstadoPedido.ENTREGADO and p.ventana_fin and p.prueba_entrega]
    dentro = sum(1 for p in con_ventana if p.prueba_entrega.registrado_en.time() <= p.ventana_fin)
    ventana = round(dentro * 100 / len(con_ventana), 1) if con_ventana else None
    bajo_minimo = [p.sku for p in db.session.query(Producto).all()
                   if p.activo and p.stock_actual <= p.stock_minimo]
check(kpis["cancelados"] == cancelados and cancelados >= 2,
      f"cancelados cuenta los anulados de hoy ({kpis['cancelados']})")
check(kpis["cumplimiento_ventana"] == ventana,
      f"cumplimiento de ventana de las entregas de hoy ({kpis['cumplimiento_ventana']} / {ventana})")

productos = datos["productos_bajo_minimo"]
skus = [p["sku"] for p in productos]
check(sorted(skus) == sorted(bajo_minimo), f"productos bajo minimo: los activos en o bajo el minimo ({len(skus)})")
check("SKU-AUTO-D" not in skus, "sin el producto inactivo")
check([p["stock_actual"] for p in productos] == sorted(p["stock_actual"] for p in productos),
      "el mas critico primero")
fila_b = next((p for p in productos if p["sku"] == "SKU-AUTO-B"), {})
check(fila_b == {"sku": "SKU-AUTO-B", "producto": "Producto SKU-AUTO-B", "stock_actual": -3,
                 "stock_minimo": 4, "negativo": True}, f"cada producto con su stock y negativo {fila_b}")
alertas = re.findall(r'<li>\s*<span class="mono">([^<]+)</span>', tablero)
check(alertas == skus[:8], "las alertas del tablero son los primeros del mismo listado")

fallidos = {f["codigo"]: f for f in datos["fallidos_para_reprogramar"]}
check({"AUTO-FAL-1", "AUTO-FAL-2"} <= set(fallidos), "incluye los fallidos de hoy con cliente con correo")
check(not {"AUTO-FAL-3", "AUTO-FAL-4"} & set(fallidos), "excluye clientes sin correo o con correo en blanco")
check("AUTO-FAL-5" not in fallidos, "excluye los fallidos de otros dias")
check(all(f["correo"] for f in fallidos.values()), "todos tienen correo")
check(fallidos.get("AUTO-FAL-1") == {"codigo": "AUTO-FAL-1", "cliente": "Supermercado El Portal",
                                     "correo": "compras@portal.invalid",
                                     "direccion": "Calle 80 #10-20, Bogota", "motivo": "Cliente ausente"},
      f"con codigo, cliente, correo, direccion y motivo {fallidos.get('AUTO-FAL-1')}")

html = datos["resumen_html"]
check(hoy().strftime("%d/%m/%Y") in html and "Tasa de éxito" in html
      and f"{kpis['tasa_exito']}%" in html, "resumen_html en espanol con la fecha y los KPIs")
check(all(s in html for s in skus) and "AUTO-FAL-1" in html and "Cliente ausente" in html,
      "con los productos bajo minimo y los fallidos")
check("<script>" not in html and "&lt;script&gt;" in html and "&lt;b&gt;incorrecta" in html,
      "los datos de clientes llegan escapados al HTML")
check("AUTO-FAL-3" not in html, "sin los fallidos que no se pueden reprogramar por correo")

app.config["CORREO_OPERACIONES"] = ""
check(resumen(TOKEN).get_json()["correo_destino"] is None, "sin CORREO_OPERACIONES, correo_destino es null")


print("\n" + "=" * 55)
print("RESULTADO: " + ("TODAS LAS PRUEBAS PASARON" if not fallos else f"{len(fallos)} FALLAS"))
for f in fallos: print("   - " + f)
sys.exit(1 if fallos else 0)
