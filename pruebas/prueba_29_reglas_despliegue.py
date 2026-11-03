"""Reglas de negocio corregidas antes del despliegue en Azure (03/11/2026).

1. Contrasenas de las cuentas demo: por variable SEMILLA_CLAVE_*; en
   produccion son obligatorias (las del README no sirven con la app en linea).
2. Reprogramar un pedido FALLIDO de un dia anterior: vuelve a PENDIENTE con
   otra fecha y sin ruta, en pantalla y por voz (antes no tenia salida).
3. Una sola ruta activa por conductor y por vehiculo en cada fecha.
4. La capacidad en unidades del vehiculo limita la ruta.
5. Un pedido nuevo (pantalla, voz o CSV) no puede tener fecha pasada.

Corre sin internet: OSRM se simula.
"""

import os
import pathlib
import re
import subprocess
import sys
from datetime import timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _preparar  # noqa: E402  Primero: base de pruebas, sin Make, Retell ni red externa.

from run import app
from app.extensions import db
from app.models import EstadoPedido, EstadoRuta, EventoPedido, Pedido, PedidoItem, Producto, Ruta, Usuario, Vehiculo
from app.services import despacho, planificacion, ruteo
from app.services import pedidos as servicio_pedidos
from app.services.importador import analizar_csv
from app.tiempo import hoy

fallos = []


def check(c, m):
    print(("  OK   " if c else "  FALLA") + f" {m}")
    if not c:
        fallos.append(m)


import requests as _requests  # noqa: E402


def _sin_red(*_, **__):
    raise _requests.ConnectionError("sin red en las pruebas")


ruteo._consultar_osrm = _sin_red
_preparar.reiniciar_base()
HOY = hoy()


def sesion(correo, clave):
    c = app.test_client()
    html = c.get("/auth/login").data.decode()
    tok = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html).group(1)
    c.post("/auth/login", data={"csrf_token": tok, "correo": correo, "contrasena": clave})
    return c


def token(c, url):
    html = c.get(url).data.decode()
    return re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html).group(1)


def pedido_nuevo(codigo, fecha, cantidad=1):
    """Copia de un pedido sembrado, PENDIENTE y sin ruta, con un solo producto."""
    base = db.session.query(Pedido).first()
    saltar = {"id", "codigo", "ruta_id", "orden_en_ruta", "estado", "fecha_despacho",
              "inventario_descontado"}
    p = Pedido(**{c.key: getattr(base, c.key) for c in Pedido.__table__.columns if c.key not in saltar},
               codigo=codigo, fecha_despacho=fecha, estado=EstadoPedido.PENDIENTE)
    db.session.add(p)
    db.session.flush()
    db.session.add(PedidoItem(pedido_id=p.id, producto_id=db.session.query(Producto).first().id,
                              cantidad=cantidad))
    db.session.commit()
    return p


print("\n== 1. Contrasenas de las cuentas demo ==")
semilla = (_preparar.RAIZ / "seed.py").read_text(encoding="utf-8")
check(all(v in semilla for v in ("SEMILLA_CLAVE_ADMIN", "SEMILLA_CLAVE_GESTOR",
                                 "SEMILLA_CLAVE_CONDUCTOR", "SEMILLA_CLAVE_CLIENTE")),
      "seed.py lee una variable SEMILLA_CLAVE_* por rol")
entorno = {k: v for k, v in os.environ.items() if not k.startswith("SEMILLA_CLAVE_")}
entorno.update(ENTORNO="produccion", SECRET_KEY="clave-secreta-de-prueba")
# La siembra solo actua sobre una base vacia.
with app.app_context():
    db.drop_all()
    db.create_all()
r = subprocess.run([sys.executable, "seed.py"], cwd=_preparar.RAIZ, env=entorno,
                   capture_output=True, text=True)
check(r.returncode != 0 and "SEMILLA_CLAVE_ADMIN" in (r.stdout + r.stderr),
      "en produccion, sin las variables, la siembra se niega y dice cuales faltan")
r = subprocess.run([sys.executable, "seed.py"], cwd=_preparar.RAIZ,
                   env={**entorno, "SEMILLA_CLAVE_ADMIN": "corta", "SEMILLA_CLAVE_GESTOR": "Gestor-2026*",
                        "SEMILLA_CLAVE_CONDUCTOR": "Conductor-2026*", "SEMILLA_CLAVE_CLIENTE": "Cliente-2026*"},
                   capture_output=True, text=True)
check(r.returncode != 0 and "politica" in (r.stdout + r.stderr),
      "una contrasena que no cumple la politica tambien se rechaza")

claves = {"SEMILLA_CLAVE_ADMIN": "Admin-Azure-2026*", "SEMILLA_CLAVE_GESTOR": "Gestor-Azure-2026*",
          "SEMILLA_CLAVE_CONDUCTOR": "Conductor-Azure-2026*", "SEMILLA_CLAVE_CLIENTE": "Cliente-Azure-2026*"}
r = subprocess.run([sys.executable, "seed.py"], cwd=_preparar.RAIZ, env={**entorno, **claves},
                   capture_output=True, text=True)
check(r.returncode == 0, "con las cuatro variables la siembra de produccion termina")
check("Admin-Azure-2026*" not in r.stdout and "Admin123*" not in r.stdout,
      "y no imprime las contrasenas (quedarian en los registros del contenedor)")
with app.app_context():
    admin = db.session.query(Usuario).filter_by(correo="admin@sgds.com").one()
    check(admin.verificar_contrasena("Admin-Azure-2026*") and not admin.verificar_contrasena("Admin123*"),
          "el administrador entra con la de su variable y no con la del README")
_preparar.reiniciar_base()

print("\n== 2. Reprogramar un fallido de un dia anterior ==")
gestor = sesion("despachador@sgds.com", "Despacho123*")
with app.app_context():
    fallido = (db.session.query(Pedido)
               .filter(Pedido.estado == EstadoPedido.FALLIDO, Pedido.fecha_despacho < HOY).first())
    check(fallido is not None, "la semilla tiene un fallido de un dia anterior")
    fid, codigo, ruta_vieja = fallido.id, fallido.codigo, fallido.ruta_id
    try:
        despacho.verificar_reintento(fallido)
        check(False, "el reintento (solo ruta de hoy) no le aplica")
    except despacho.TransicionInvalida:
        check(True, "el reintento (solo ruta de hoy) no le aplica")
    for fecha, nombre in ((HOY - timedelta(days=1), "una fecha pasada"), (None, "sin fecha")):
        try:
            despacho.verificar_reprogramacion(fallido, fecha)
            check(False, f"reprogramar con {nombre} se rechaza")
        except despacho.TransicionInvalida:
            check(True, f"reprogramar con {nombre} se rechaza")

html = gestor.get(f"/pedidos/{fid}").data.decode()
check(f"/pedidos/{fid}/reprogramar" in html, "el detalle de un fallido muestra Reprogramar")
manana = HOY + timedelta(days=1)
r = gestor.post(f"/pedidos/{fid}/reprogramar",
                data={"csrf_token": token(gestor, f"/pedidos/{fid}"), "fecha": manana.isoformat()},
                follow_redirects=True)
check("reprogramado" in r.data.decode(), "en pantalla se reprograma con una fecha futura")
with app.app_context():
    p = db.session.get(Pedido, fid)
    check(p.estado == EstadoPedido.PENDIENTE and p.ruta_id is None and p.fecha_despacho == manana,
          "queda PENDIENTE, sin ruta y con la nueva fecha")
    evento = db.session.query(EventoPedido).filter_by(pedido_id=fid).order_by(EventoPedido.id.desc()).first()
    check(evento.estado_anterior == EstadoPedido.FALLIDO and evento.nota.startswith("Reprogramado para el"),
          "deja el evento en la bitacora del pedido")
    check(db.session.get(Ruta, ruta_vieja) is not None, "la ruta anterior se conserva")
html = gestor.get(f"/pedidos/{fid}").data.decode()
check(f"/pedidos/{fid}/reprogramar" not in html, "un pedido pendiente ya no muestra Reprogramar")
conductor = sesion("conductor1@sgds.com", "Conductor123*")
check(conductor.post(f"/pedidos/{fid}/reprogramar", data={"fecha": manana.isoformat()}).status_code in (302, 400, 403),
      "un conductor no puede reprogramar")

print("\n== 3. Una ruta activa por conductor y por vehiculo ==")
dia = HOY + timedelta(days=5)
with app.app_context():
    a, b, c3 = (pedido_nuevo(f"T29-{i}", dia) for i in "ABC")
    c1 = db.session.query(Usuario).filter_by(correo="conductor1@sgds.com").one()
    c2 = db.session.query(Usuario).filter_by(correo="conductor2@sgds.com").one()
    veh = db.session.query(Vehiculo).filter_by(placa="WGT-482").one()
    gestor_id = db.session.query(Usuario).filter_by(correo="despachador@sgds.com").one().id
    ruta1, _ = planificacion.crear_ruta({"fecha": dia.isoformat(), "conductor_id": c1.id,
                                          "vehiculo_id": veh.id, "pedidos": [a.id]}, gestor_id)
    db.session.commit()
    try:
        planificacion.crear_ruta({"fecha": dia.isoformat(), "conductor_id": c1.id, "pedidos": [b.id]}, gestor_id)
        check(False, "una segunda ruta activa del mismo conductor se rechaza")
    except Exception as error:
        db.session.rollback()
        check(ruta1.codigo in str(getattr(error, "errores", error)) or ruta1.codigo in repr(error),
              "una segunda ruta activa del mismo conductor se rechaza, nombrando la existente")
    try:
        planificacion.crear_ruta({"fecha": dia.isoformat(), "conductor_id": c2.id, "vehiculo_id": veh.id,
                                  "pedidos": [b.id]}, gestor_id)
        check(False, "el mismo vehiculo en dos rutas activas del dia se rechaza")
    except Exception:
        db.session.rollback()
        check(True, "el mismo vehiculo en dos rutas activas del dia se rechaza")
    ruta2, _ = planificacion.crear_ruta({"fecha": dia.isoformat(), "conductor_id": c2.id,
                                          "pedidos": [b.id]}, gestor_id)
    db.session.commit()
    check(ruta2.id is not None, "otro conductor sin vehiculo repetido si puede tener su ruta")
    planificacion.agregar_a_ruta(db.session.get(Pedido, c3.id), ruta1, gestor_id)
    db.session.commit()
    check(db.session.get(Pedido, c3.id).ruta_id == ruta1.id,
          "los pedidos adicionales se agregan a la ruta existente")

print("\n== 4. Capacidad en unidades del vehiculo ==")
dia4 = HOY + timedelta(days=6)
with app.app_context():
    veh = db.session.query(Vehiculo).filter_by(placa="WGT-482").one()
    c1 = db.session.query(Usuario).filter_by(correo="conductor1@sgds.com").one()
    gestor_id = db.session.query(Usuario).filter_by(correo="despachador@sgds.com").one().id
    grande = pedido_nuevo("T29-GRANDE", dia4, veh.capacidad_unidades + 1)
    try:
        planificacion.crear_ruta({"fecha": dia4.isoformat(), "conductor_id": c1.id, "vehiculo_id": veh.id,
                                  "pedidos": [grande.id]}, gestor_id)
        check(False, "una ruta que excede la capacidad del vehiculo se rechaza")
    except Exception as error:
        db.session.rollback()
        check("unidades" in str(getattr(error, "errores", error)) or "unidades" in repr(error),
              "una ruta que excede la capacidad del vehiculo se rechaza, con las unidades")
    justo = pedido_nuevo("T29-JUSTO", dia4, veh.capacidad_unidades)
    ruta, _ = planificacion.crear_ruta({"fecha": dia4.isoformat(), "conductor_id": c1.id,
                                         "vehiculo_id": veh.id, "pedidos": [justo.id]}, gestor_id)
    db.session.commit()
    check(ruta.id is not None, "con la carga exacta de su capacidad si se crea")
    extra = pedido_nuevo("T29-EXTRA", dia4, 1)
    try:
        planificacion.agregar_a_ruta(extra, ruta, gestor_id)
        check(False, "agregar un pedido que ya no cabe se rechaza")
    except planificacion.PlanificacionInvalida:
        db.session.rollback()
        check(True, "agregar un pedido que ya no cabe se rechaza")

print("\n== 5. Fecha de despacho no anterior a hoy ==")
with app.app_context():
    prod = db.session.query(Producto).first()
    _, errores = servicio_pedidos.validar_pedido(items=[(prod.id, 1)], fecha_despacho=HOY - timedelta(days=1),
                                                 prioridad=2)
    check(any(campo == "fecha_despacho" for campo, _ in errores), "un pedido nuevo con fecha pasada se rechaza")
    _, errores = servicio_pedidos.validar_pedido(items=[(prod.id, 1)], fecha_despacho=HOY, prioridad=2)
    check(not errores, "con fecha de hoy se acepta")
    csv = (f"cliente_nombre,direccion,sku,cantidad,fecha_despacho\n"
           f"Tienda Vieja,Calle 1,{prod.sku},2,{(HOY - timedelta(days=3)).isoformat()}\n"
           f"Tienda Nueva,Calle 2,{prod.sku},2,{(HOY + timedelta(days=3)).isoformat()}\n").encode()
    resultado = analizar_csv(csv)
    texto = repr(resultado.__dict__ if hasattr(resultado, "__dict__") else resultado)
    check("anterior a hoy" in texto, "el CSV marca la fila con fecha pasada")

print("\n" + "=" * 55)
print("RESULTADO: " + ("TODAS LAS PRUEBAS PASARON" if not fallos else f"{len(fallos)} FALLAS"))
for f in fallos:
    print("   - " + f)
sys.exit(1 if fallos else 0)
