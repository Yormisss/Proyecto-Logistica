"""Reglas de pantalla extraidas a servicios: NIT fijo, sedes, SKU, vehiculos,
edicion de pedidos y bitacora de rutas. Son las mismas que usa el asistente.

Corre sin internet: el ruteo se simula sin red.
"""

import html as html_lib
import pathlib
import re
import sys
from datetime import date, timedelta
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _preparar  # noqa: E402  Primero: base de pruebas, sin Make, Retell ni red externa.
import requests

from run import app
from app.extensions import db
from app.models import (Cliente, DireccionCliente, EstadoPedido, EstadoRuta, EventoPedido,
                        MovimientoInventario, Pedido, PedidoItem, Producto, Ruta, TipoMovimiento, Usuario,
                        Vehiculo)
from app.services import ruteo
from app.services.inventario import registrar_movimiento
from app.services.busqueda_voz import buscar_sedes
from app.tiempo import hoy

fallos = []
def check(c, m):
    print(("  OK   " if c else "  FALLA") + f" {m}")
    if not c: fallos.append(m)

_preparar.reiniciar_base()

app.config["WTF_CSRF_ENABLED"] = True
app.config.update(MAKE_WEBHOOK_URL="", CORREO_OPERACIONES="", RETELL_API_KEY="")
def _sin_red(*args, **kwargs):
    raise requests.ConnectionError("sin red en las pruebas")
ruteo._consultar_osrm = _sin_red


def sesion(correo, clave):
    c = app.test_client()
    html = c.get("/auth/login").data.decode()
    tok = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html).group(1)
    c.post("/auth/login", data={"csrf_token": tok, "correo": correo, "contrasena": clave})
    return c

def token(c, url):
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', c.get(url).data.decode())
    return m.group(1) if m else ""

def texto(r):
    return html_lib.unescape(r.data.decode())

def enviar(c, url, datos, url_formulario=None):
    return c.post(url, data={"csrf_token": token(c, url_formulario or url), **datos}, follow_redirects=True)

gestor = sesion("despachador@sgds.com", "Despacho123*")
conductor = sesion("conductor1@sgds.com", "Conductor123*")

def cliente_db(nombre):
    with app.app_context():
        c = db.session.query(Cliente).filter_by(nombre=nombre).one_or_none()
        return SimpleNamespace(id=c.id, documento=c.documento, activo=c.activo) if c else None


print("\n== 1. Clientes: NIT fijo una vez registrado ==")
datos_cliente = {"nombre": "Ferreteria Central", "documento": "", "telefono": "3001112233",
                 "correo": "compras@ferreteriacentral.com.co", "activo": "y"}
r = enviar(gestor, "/admin/clientes/nuevo", datos_cliente)
c = cliente_db("Ferreteria Central")
check(c is not None and c.documento is None, "se crea un cliente sin NIT")
url_editar = f"/admin/clientes/{c.id}/editar"
check("Una vez registrado, no se puede cambiar" in texto(gestor.get(url_editar)), "la pantalla avisa que el NIT queda fijo")
r = enviar(gestor, url_editar, {**datos_cliente, "documento": "900555111-2"})
check(cliente_db("Ferreteria Central").documento == "900555111-2", "si no tenia NIT, se registra")
html = texto(gestor.get(url_editar))
check('name="documento"' in html and "readonly" in html.split('name="documento"')[0][-200:] + html.split('name="documento"')[1][:200]
      and "El NIT queda fijo una vez registrado." in html, "despues el campo queda de solo lectura")
r = enviar(gestor, url_editar, {**datos_cliente, "documento": "900999999-9"})
check("El NIT queda fijo una vez registrado." in texto(r) and cliente_db("Ferreteria Central").documento == "900555111-2",
      "un POST que intenta cambiar el NIT se rechaza")
r = enviar(gestor, "/admin/clientes/nuevo", {**datos_cliente, "nombre": "Otra Ferreteria", "documento": "900555111-2"})
check("Ya existe un cliente con este NIT." in texto(r), "el NIT sigue siendo unico")
r = enviar(gestor, "/admin/clientes/nuevo", {**datos_cliente, "nombre": "Otra", "correo": "no-es-correo"})
check("El correo no es válido." in texto(r), "valida el correo")


print("\n== 2. Sedes: crear, editar, coordenadas, ventana y desactivar ==")
url_detalle = f"/admin/clientes/{c.id}"
def sede(**datos):
    base = {"etiqueta": "", "direccion": "", "ciudad": "Bogota", "latitud": "", "longitud": "",
            "ventana_inicio": "", "ventana_fin": ""}
    return enviar(gestor, url_detalle, {**base, **datos})
for datos, esperado, que in (
        ({"direccion": "Calle 10 #5-20", "ventana_inicio": "14:00", "ventana_fin": "09:00"},
         "La hora final debe ser posterior a la inicial.", "con la ventana invertida"),
        ({"direccion": "Calle 10 #5-20", "ventana_inicio": "08:00"},
         "La ventana horaria necesita la hora inicial y la final.", "con una sola hora"),
        ({"direccion": "Calle 10 #5-20", "latitud": "4.6"}, "Las coordenadas necesitan la latitud y la longitud.",
         "con una sola coordenada"),
        ({"direccion": "Calle 10 #5-20", "latitud": "95", "longitud": "-74"}, "La latitud debe ser un número",
         "con la latitud fuera de rango"),
        ({"direccion": ""}, "Indique la dirección.", "sin direccion")):
    r = sede(**datos)
    check(esperado in texto(r), f"rechaza una sede {que}")
r = sede(etiqueta="Principal", direccion="Calle 10 #5-20", latitud="4,61", longitud="-74.08",
         ventana_inicio="08:00", ventana_fin="12:00")
check("Sede 'Principal' agregada." in texto(r), "crea una sede con coordenadas (coma decimal) y ventana")
r = sede(etiqueta="Bodega", direccion="Calle 80 #20-10")
with app.app_context():
    sedes = {s.etiqueta: s for s in db.session.get(Cliente, c.id).direcciones}
    principal, bodega = sedes["Principal"], sedes["Bodega"]
    check(principal.latitud == 4.61 and principal.longitud == -74.08
          and principal.ventana_inicio.strftime("%H:%M") == "08:00", "con sus datos guardados")
    principal_id, bodega_id = principal.id, bodega.id
    # Un pedido de la sede principal, para verificar que conserva su copia.
    gestor_id = db.session.query(Usuario).filter_by(correo="despachador@sgds.com").one().id
    producto_id = db.session.query(Producto).filter_by(sku="SKU-1001").one().id
    ped = Pedido(codigo="REG-001", cliente_id=c.id, direccion_id=principal_id, cliente_nombre="Ferreteria Central",
                 direccion="Calle 10 #5-20", ciudad="Bogota", fecha_despacho=hoy(), estado=EstadoPedido.PENDIENTE,
                 prioridad=3, creado_por_id=gestor_id)
    db.session.add(ped); db.session.flush()
    db.session.add(PedidoItem(pedido_id=ped.id, producto_id=producto_id, cantidad=2))
    db.session.commit()
    pedido_id = ped.id
r = sede(etiqueta="Otra", direccion="calle 10 # 5 20")
check("El cliente ya tiene registrada esa direccion." in texto(r), "no repite una direccion del cliente")

url_sede = f"/admin/clientes/{c.id}/sedes/{principal_id}/editar"
check(gestor.get(url_sede).status_code == 200, "la sede tiene pantalla de edicion")
r = enviar(gestor, url_sede, {"etiqueta": "Principal", "direccion": "Calle 12 #5-40", "ciudad": "Bogota",
                              "latitud": "", "longitud": "", "ventana_inicio": "07:30", "ventana_fin": "11:00"})
with app.app_context():
    s = db.session.get(DireccionCliente, principal_id)
    p = db.session.get(Pedido, pedido_id)
    check(s.direccion == "Calle 12 #5-40" and s.latitud is None and s.ventana_inicio.strftime("%H:%M") == "07:30",
          "editar la sede cambia direccion, coordenadas y ventana")
    check(p.direccion == "Calle 10 #5-20", "el pedido ya creado conserva su copia historica de la direccion")
r = enviar(gestor, url_sede, {"etiqueta": "Principal", "direccion": "Calle 80 #20-10", "ciudad": "Bogota"})
check("El cliente ya tiene registrada esa direccion." in texto(r), "editar tampoco repite la direccion de otra sede")

r = enviar(gestor, f"/admin/clientes/{c.id}/sedes/{bodega_id}/estado", {}, url_detalle)
check("desactivada: no se ofrece para pedidos nuevos" in texto(r), "desactivar una sede")
with app.app_context():
    ofrecidas = [s.etiqueta for s in buscar_sedes(db.session.get(Cliente, c.id)).elementos]
check(ofrecidas == ["Principal"], f"una sede desactivada no se ofrece para pedidos ({ofrecidas})")
check("Bodega" not in texto(gestor.get(f"/pedidos/{pedido_id}/editar")).split('name="sede_id"')[1].split("</select>")[0],
      "ni en la edicion de pedidos")
r = enviar(gestor, f"/admin/clientes/{c.id}/sedes/{bodega_id}/estado", {}, url_detalle)
check("Sede 'Bodega' activada." in texto(r), "y se puede volver a activar")


print("\n== 3. Productos: SKU fijo con movimientos ==")
def producto_db(sku):
    with app.app_context():
        p = db.session.query(Producto).filter_by(sku=sku).one_or_none()
        return SimpleNamespace(id=p.id, sku=p.sku, stock=p.stock_actual) if p else None
base_producto = {"nombre": "Tornillos", "descripcion": "", "unidad": "UND", "stock_minimo": "5", "activo": "y"}
enviar(gestor, "/inventario/nuevo", {**base_producto, "sku": "tor-001", "stock_inicial": "0"})
sin_mov = producto_db("TOR-001")
check(sin_mov is not None and sin_mov.stock == 0, "un producto creado sin stock no tiene movimientos")
r = enviar(gestor, f"/inventario/{sin_mov.id}/editar", {**base_producto, "sku": "TOR-002"})
check(producto_db("TOR-002") is not None, "mientras no tenga movimientos, el SKU se puede cambiar")
enviar(gestor, "/inventario/nuevo", {**base_producto, "sku": "TUE-001", "nombre": "Tuercas", "stock_inicial": "40"})
con_mov = producto_db("TUE-001")
check(con_mov.stock == 40, "un producto creado con stock inicial entra con su movimiento")
html = texto(gestor.get(f"/inventario/{con_mov.id}/editar"))
check("El SKU queda fijo porque el producto ya tiene movimientos de inventario." in html, "la pantalla lo muestra fijo")
r = enviar(gestor, f"/inventario/{con_mov.id}/editar", {**base_producto, "sku": "TUE-999", "nombre": "Tuercas"})
check("El SKU queda fijo porque el producto ya tiene movimientos de inventario." in texto(r)
      and producto_db("TUE-999") is None, "un POST que intenta cambiarlo se rechaza")
r = enviar(gestor, "/inventario/nuevo", {**base_producto, "sku": "TUE-001", "stock_inicial": "0"})
check("Ya existe un producto con este SKU" in texto(r), "el SKU sigue siendo unico")

print("\n== 3b. Productos: el stock solo cambia con movimientos ==")
def movimientos_de(producto_id):
    with app.app_context():
        return db.session.query(MovimientoInventario).filter_by(producto_id=producto_id).count()
html = texto(gestor.get(f"/inventario/{con_mov.id}/editar"))
check('name="stock_inicial"' not in html and 'name="stock_actual"' not in html
      and "registrar un movimiento" in html,
      "la edicion muestra el stock sin campo para cambiarlo, con el enlace a los movimientos")
check('name="stock_inicial"' in texto(gestor.get("/inventario/nuevo")), "el alta si pide el stock inicial")
movs = movimientos_de(con_mov.id)
enviar(gestor, f"/inventario/{con_mov.id}/editar", {**base_producto, "sku": "TUE-001", "nombre": "Tuercas M8",
                                                    "stock_actual": "7"})
editado = producto_db("TUE-001")
with app.app_context():
    nombre_editado = db.session.get(Producto, con_mov.id).nombre
check(nombre_editado == "Tuercas M8" and editado.stock == 40 and movimientos_de(con_mov.id) == movs,
      "un POST de edicion que trae stock_actual cambia el nombre pero no el stock ni crea movimientos")
url_detalle_producto = f"/inventario/{con_mov.id}"
r = enviar(gestor, url_detalle_producto, {"tipo": "AJUSTE", "cantidad": "30", "motivo": "  "}, url_detalle_producto)
check("Indique el motivo del ajuste." in texto(r) and producto_db("TUE-001").stock == 40
      and movimientos_de(con_mov.id) == movs, "un ajuste sin motivo se rechaza en pantalla")
r = enviar(gestor, url_detalle_producto, {"tipo": "ENTRADA", "cantidad": "0", "motivo": ""}, url_detalle_producto)
check("al menos una unidad" in texto(r) and movimientos_de(con_mov.id) == movs, "una entrada de cero se rechaza")
r = enviar(gestor, url_detalle_producto, {"tipo": "ENTRADA", "cantidad": "5", "motivo": ""}, url_detalle_producto)
check(producto_db("TUE-001").stock == 45, "una entrada no exige motivo")
r = enviar(gestor, url_detalle_producto, {"tipo": "AJUSTE", "cantidad": "0", "motivo": "Conteo: no quedan"},
           url_detalle_producto)
with app.app_context():
    ultimo = (db.session.query(MovimientoInventario).filter_by(producto_id=con_mov.id)
              .order_by(MovimientoInventario.id.desc()).first())
    check(db.session.get(Producto, con_mov.id).stock_actual == 0 and ultimo.tipo == TipoMovimiento.AJUSTE
          and ultimo.motivo == "Conteo: no quedan", "un ajuste con motivo puede dejar el stock en cero")
    try:
        registrar_movimiento(db.session.get(Producto, con_mov.id), TipoMovimiento.AJUSTE, 3, None)
        sin_motivo = False
    except ValueError:
        sin_motivo = True
    db.session.rollback()
check(sin_motivo, "el servicio tampoco acepta un ajuste sin motivo")


print("\n== 4. Vehiculos ==")
check(gestor.get("/admin/vehiculos/").status_code == 200, "el gestor administra vehiculos")
check(conductor.get("/admin/vehiculos/").status_code in (302, 403), "el conductor no")
check('href="/admin/vehiculos/"' in texto(gestor.get("/admin/")), "con su enlace en el menu")
def vehiculo(url, **datos):
    base = {"placa": "", "tipo": "Furgon", "capacidad_kg": "900", "capacidad_unidades": "300",
            "conductor_id": "0", "activo": "y"}
    return enviar(gestor, url, {**base, **datos})
r = vehiculo("/admin/vehiculos/nuevo", placa="wgt 482")
check("Ya existe un vehículo con la placa WGT-482." in texto(r), "la placa se compara sin espacios ni guiones")
with app.app_context():
    c1 = db.session.query(Usuario).filter_by(correo="conductor1@sgds.com").one()
    c1_id, c1_placa = c1.id, c1.vehiculo.placa
r = vehiculo("/admin/vehiculos/nuevo", placa="xyz 789", conductor_id=str(c1_id))
check(f"ya tiene asignado el vehículo {c1_placa}" in texto(r), "un conductor no puede tener dos vehiculos")
r = vehiculo("/admin/vehiculos/nuevo", placa="xyz 789", capacidad_kg="-1")
check("La capacidad en kilos debe ser un número mayor o igual a 0." in texto(r), "valida las capacidades")
r = vehiculo("/admin/vehiculos/nuevo", placa="xyz 789", tipo="Moto")
with app.app_context():
    nuevo = db.session.query(Vehiculo).filter_by(placa="XYZ 789").one_or_none()
    nuevo_id = nuevo.id if nuevo else None
check("Vehiculo XYZ 789 registrado." in texto(r) and nuevo_id, "registra el vehiculo con su placa en mayusculas")
r = vehiculo(f"/admin/vehiculos/{nuevo_id}/editar", placa="AAA 111", tipo="Moto")
with app.app_context():
    check(db.session.get(Vehiculo, nuevo_id).placa == "XYZ 789", "la placa queda fija al editar")
r = vehiculo(f"/admin/vehiculos/{nuevo_id}/editar", placa="XYZ 789", tipo="Camion")
with app.app_context():
    check(db.session.get(Vehiculo, nuevo_id).tipo == "Camion", "los demas datos se editan")
    ruta_hoy = db.session.query(Ruta).filter(Ruta.fecha == hoy(), Ruta.vehiculo_id.isnot(None)).first()
    con_ruta_id = ruta_hoy.vehiculo_id
r = enviar(gestor, f"/admin/vehiculos/{con_ruta_id}/estado", {}, "/admin/vehiculos/")
with app.app_context():
    check("No se puede desactivar un vehículo que está en una ruta abierta." in texto(r)
          and db.session.get(Vehiculo, con_ruta_id).activo, "no se desactiva un vehiculo en una ruta abierta")
r = enviar(gestor, f"/admin/vehiculos/{nuevo_id}/estado", {}, "/admin/vehiculos/")
check("Vehiculo XYZ 789 desactivado." in texto(r), "uno libre si se desactiva")


print("\n== 5. Edicion de pedidos ==")
url_ped = f"/pedidos/{pedido_id}/editar"
check("Editar" in texto(gestor.get(f"/pedidos/{pedido_id}")), "un pedido PENDIENTE muestra Editar")
def editar_pedido(url=url_ped, **datos):
    with app.app_context():
        p = db.session.get(Pedido, int(url.split("/")[2]))
        base = {"fecha_despacho": p.fecha_despacho.isoformat(), "sede_id": str(p.direccion_id or 0),
                "ventana_inicio": p.ventana_inicio.strftime("%H:%M") if p.ventana_inicio else "",
                "ventana_fin": p.ventana_fin.strftime("%H:%M") if p.ventana_fin else "",
                "prioridad": str(p.prioridad),
                "observaciones": p.observaciones or ""}
        items = [(i.producto_id, i.cantidad) for i in p.items]
    datos = {**base, **datos}
    if "producto_id" not in datos:
        datos["producto_id"] = [str(x[0]) for x in items]
        datos["cantidad"] = [str(x[1]) for x in items]
    return enviar(gestor, url, datos)
r = editar_pedido(fecha_despacho=(hoy() - timedelta(days=1)).isoformat())
check("La fecha de despacho no puede ser anterior a hoy." in texto(r), "no acepta una fecha anterior a hoy")
r = editar_pedido(ventana_inicio="15:00", ventana_fin="10:00")
check("La hora final debe ser posterior a la inicial." in texto(r), "valida la ventana")
with app.app_context():
    otra_sede = db.session.query(DireccionCliente).filter(DireccionCliente.cliente_id != c.id).first().id
r = editar_pedido(sede_id=str(otra_sede))
check("La sede debe ser una de las sedes del mismo cliente." in texto(r), "solo una sede del mismo cliente")
r = editar_pedido()
check("No hay cambios para guardar." in texto(r), "sin cambios avisa")
manana = hoy() + timedelta(days=1)
r = editar_pedido(fecha_despacho=manana.isoformat(), sede_id=str(bodega_id), ventana_inicio="09:00",
                  ventana_fin="11:00", prioridad="1", observaciones="Llamar antes",
                  producto_id=[str(producto_id)], cantidad=["5"])
with app.app_context():
    p = db.session.get(Pedido, pedido_id)
    nota = sorted(p.eventos, key=lambda e: e.id)[-1].nota
    check("Pedido REG-001 actualizado." in texto(r) and p.fecha_despacho == manana and p.prioridad == 1
          and p.direccion == "Calle 80 #20-10" and p.direccion_id == bodega_id and p.observaciones == "Llamar antes"
          and [(i.producto_id, i.cantidad) for i in p.items] == [(producto_id, 5)],
          "PENDIENTE: cambia fecha, sede (con su copia), ventana, prioridad, observaciones y productos")
    check(nota.startswith("Pedido editado: ") and "prioridad: baja -> alta" in nota and "productos actualizados" in nota,
          f"la edicion queda en la bitacora del pedido: {nota}")
    # Pasa a ASIGNADO.
    p.estado = EstadoPedido.ASIGNADO
    db.session.commit()
r = editar_pedido(fecha_despacho=(manana + timedelta(days=1)).isoformat())
check("No hay cambios para guardar." in texto(r), "ASIGNADO: la pantalla no ofrece la fecha y la ignora")
with app.app_context():
    from app.services.pedidos import editar_pedido as editar_servicio
    from app.services.validacion import DatosInvalidos
    try:
        editar_servicio(db.session.get(Pedido, pedido_id), {"fecha_despacho": manana + timedelta(days=1)}, gestor_id)
        rechazo = ""
    except DatosInvalidos as error:
        rechazo = str(error)
    db.session.rollback()
check("no se puede cambiar la fecha; solo la ventana, la prioridad y las observaciones" in rechazo,
      f"ASIGNADO: el servicio (el que usa la voz) rechaza cambiar la fecha: {rechazo}")
r = editar_pedido(prioridad="2")
check("Pedido REG-001 actualizado." in texto(r) and "conviene recalcularla" in texto(r),
      "ASIGNADO: cambia la prioridad y advierte recalcular la ruta")
with app.app_context():
    p = db.session.get(Pedido, pedido_id)
    p.estado = EstadoPedido.EN_RUTA
    db.session.commit()
r = gestor.get(url_ped, follow_redirects=True)
check("Un pedido en ruta no se puede editar." in texto(r), "EN_RUTA no se edita")
check("Editar</a>" not in texto(gestor.get(f"/pedidos/{pedido_id}")), "y su detalle no muestra Editar")


print("\n== 6. Rutas: quitar y eliminar dejan bitacora ==")
with app.app_context():
    ruta = db.session.query(Ruta).filter(Ruta.fecha == hoy()).first()
    abiertas = [p for p in ruta.pedidos if p.estado == EstadoPedido.ASIGNADO]
    quitar_id, ruta_id, codigo_ruta = abiertas[0].id, ruta.id, ruta.codigo
enviar(gestor, f"/rutas/{ruta_id}/quitar/{quitar_id}", {}, f"/rutas/{ruta_id}")
with app.app_context():
    ultimo = sorted(db.session.get(Pedido, quitar_id).eventos, key=lambda e: e.id)[-1]
    check(ultimo.nota == f"Retirado de la ruta {codigo_ruta}" and ultimo.estado_nuevo == EstadoPedido.PENDIENTE,
          "quitar una parada queda en la bitacora")
    nueva = Ruta(codigo="RUT-REG-01", fecha=hoy(), estado=EstadoRuta.PLANIFICADA, conductor_id=c1_id)
    db.session.add(nueva); db.session.flush()
    db.session.get(Pedido, quitar_id).ruta_id = nueva.id
    db.session.get(Pedido, quitar_id).estado = EstadoPedido.ASIGNADO
    db.session.commit()
    nueva_id = nueva.id
r = enviar(gestor, f"/rutas/{nueva_id}/eliminar", {}, f"/rutas/{nueva_id}")
with app.app_context():
    ultimo = sorted(db.session.get(Pedido, quitar_id).eventos, key=lambda e: e.id)[-1]
    check("RUT-REG-01 eliminada" in texto(r) and ultimo.nota == "Ruta RUT-REG-01 eliminada"
          and db.session.get(Pedido, quitar_id).estado == EstadoPedido.PENDIENTE,
          "eliminar una ruta deja el evento en la bitacora de cada pedido")


print("\n" + "=" * 55)
print("RESULTADO: " + ("TODAS LAS PRUEBAS PASARON" if not fallos else f"{len(fallos)} FALLAS"))
for f in fallos: print("   - " + f)
sys.exit(1 if fallos else 0)
