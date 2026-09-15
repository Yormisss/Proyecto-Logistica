import pathlib
import json, re, sys
from datetime import date, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from run import app
from app.extensions import db
from app.models import Pedido, Ruta, Usuario, Rol, EstadoPedido, EstadoRuta, EventoPedido

fallos=[]
def check(c,m):
    print(("  OK   " if c else "  FALLA")+f" {m}")
    if not c: fallos.append(m)
app.config["WTF_CSRF_ENABLED"]=True   # se prueba con CSRF activo

def sesion(correo, clave):
    c = app.test_client()
    html = c.get("/auth/login").data.decode()
    tok = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html).group(1)
    c.post("/auth/login", data={"csrf_token":tok,"correo":correo,"contrasena":clave})
    return c

def token(c, url):
    html = c.get(url).data.decode()
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    return m.group(1) if m else None

c = sesion("despachador@sgds.com","Despacho123*")

print("\n== 1. Acceso a las vistas ==")
for url in ["/rutas/","/rutas/nueva"]:
    check(c.get(url).status_code==200, f"{url} carga")
check(b"Rutas" in c.get("/", follow_redirects=True).data, "el menu muestra Rutas")

print("\n== 2. Pedidos disponibles para planificar ==")
# Crear pedidos pendientes geolocalizados para hoy
hoy = date.today()
with app.app_context():
    despachador = db.session.query(Usuario).filter_by(correo="despachador@sgds.com").first()
    datos = [("Cliente Norte",4.7014,-74.0713,time(14,0)),
             ("Cliente Sur",4.6285,-74.1465,time(17,0)),
             ("Cliente Centro",4.6412,-74.0637,time(9,0)),
             ("Cliente Sin Geo",None,None,None)]
    ids=[]
    for i,(n,la,lo,vf) in enumerate(datos, start=100):
        p = Pedido(codigo=f"TEST-{i}", cliente_nombre=n, direccion=f"Direccion {i}",
                   latitud=la, longitud=lo, ventana_fin=vf, fecha_despacho=hoy,
                   estado=EstadoPedido.PENDIENTE, creado_por_id=despachador.id, prioridad=2)
        db.session.add(p); db.session.flush(); ids.append(p.id)
    db.session.commit()
    conductor = db.session.query(Usuario).filter_by(correo="conductor2@sgds.com").first()
    cond_id = conductor.id

html = c.get(f"/rutas/nueva?fecha={hoy.isoformat()}").data.decode()
check("TEST-100" in html and "TEST-103" in html, "lista los pedidos pendientes")
check(html.count('name="pedido_id"') >= 4, "cada pedido tiene su casilla")
fila_sin_geo = [f for f in re.findall(r"<tr>.*?</tr>", html, re.S) if "TEST-103" in f]
check(bool(fila_sin_geo) and "etiqueta-fallido" in fila_sin_geo[0] and "No entra en el calculo" in fila_sin_geo[0],
      "marca el pedido sin coordenadas")

print("\n== 3. Generacion de ruta (RF3) ==")
tok = token(c, f"/rutas/nueva?fecha={hoy.isoformat()}")
r = c.post("/rutas/nueva", data={
    "csrf_token":tok, "fecha":hoy.isoformat(), "conductor_id":str(cond_id),
    "vehiculo_id":"0", "estrategia":"DISTANCIA",
    "pedido_id":[str(i) for i in ids],
}, follow_redirects=True)
check(b"creada con 4 parada" in r.data, "crea la ruta con 4 paradas")
check(b"advertencia" in r.data or b"sin coordenadas" in r.data, "advierte sobre el pedido sin geo")

with app.app_context():
    ruta = db.session.query(Ruta).filter_by(conductor_id=cond_id, fecha=hoy).order_by(Ruta.id.desc()).first()
    rid = ruta.id
    check(ruta.codigo.startswith("RUT-"), f"codigo generado: {ruta.codigo}")
    check(ruta.proveedor_ruteo=="OSRM", f"uso OSRM: {ruta.proveedor_ruteo}")
    check(ruta.distancia_km>0 and ruta.duracion_min>0, f"{ruta.distancia_km} km / {ruta.duracion_min} min")
    check(ruta.geometria is not None and len(json.loads(ruta.geometria))>10, "guarda la geometria")
    ords = sorted(p.orden_en_ruta for p in ruta.pedidos)
    check(ords==[1,2,3,4], f"secuencia sin huecos ni repetidos: {ords}")
    check(all(p.estado==EstadoPedido.ASIGNADO for p in ruta.pedidos), "pedidos pasan a ASIGNADO")
    sin_geo = [p for p in ruta.pedidos if not p.tiene_coordenadas][0]
    check(sin_geo.orden_en_ruta==4, f"el pedido sin geo queda ultimo (pos {sin_geo.orden_en_ruta})")
    ev = db.session.query(EventoPedido).filter_by(pedido_id=ids[0], estado_nuevo=EstadoPedido.ASIGNADO).first()
    check(ev is not None and "RUT-" in ev.nota, "registra evento de asignacion")

print("\n== 4. Detalle y mapa ==")
html = c.get(f"/rutas/{rid}").data.decode()
check("mapa-ruta" in html and "leaflet" in html.lower(), "renderiza el contenedor del mapa")
m = re.search(r'<script id="datos-mapa" type="application/json">(.*?)</script>', html, re.S)
check(m is not None, "inyecta los datos del mapa")
if m:
    datos = json.loads(json.loads(m.group(1)) if m.group(1).strip().startswith('"') else m.group(1))
    check(len(datos["paradas"])==3, f"3 paradas geolocalizadas en el mapa (el 4o no tiene coords): {len(datos['paradas'])}")
    check(datos["origen"]["lat"]==app.config["CD_LAT"], "el origen es el centro de distribucion")
    check(datos["geometria"] and len(datos["geometria"])>10, "geometria disponible para dibujar")
    check(all(0<p["orden"]<=4 for p in datos["paradas"]), "cada parada lleva su numero de orden")

print("\n== 5. Los pedidos ya no estan disponibles ==")
html = c.get(f"/rutas/nueva?fecha={hoy.isoformat()}").data.decode()
check("TEST-100" not in html, "los pedidos asignados salen de la bolsa de disponibles")

print("\n== 6. Recalculo por ventana horaria ==")
tok = token(c, f"/rutas/{rid}")
with app.app_context():
    antes = db.session.get(Ruta, rid).distancia_km
r = c.post(f"/rutas/{rid}/recalcular", data={"csrf_token":tok,"estrategia":"VENTANA"}, follow_redirects=True)
check(b"Ruta recalculada" in r.data, "recalcula la ruta")
with app.app_context():
    ruta = db.session.get(Ruta, rid)
    geo = [p for p in ruta.pedidos if p.tiene_coordenadas]
    sec = sorted(geo, key=lambda p: p.orden_en_ruta)
    horas = [p.ventana_fin for p in sec if p.ventana_fin]
    check(horas == sorted(horas), f"orden respeta las ventanas: {[str(h) for h in horas]}")
    check(ruta.distancia_km != antes, f"la distancia cambio: {antes} -> {ruta.distancia_km} km")

print("\n== 7. Quitar una parada ==")
tok = token(c, f"/rutas/{rid}")
r = c.post(f"/rutas/{rid}/quitar/{ids[0]}", data={"csrf_token":tok}, follow_redirects=True)
check(b"devuelto a la bolsa de pendientes" in r.data, "retira la parada")
with app.app_context():
    p = db.session.get(Pedido, ids[0])
    check(p.ruta_id is None and p.estado==EstadoPedido.PENDIENTE and p.orden_en_ruta is None, "el pedido vuelve a pendiente")

print("\n== 8. Protecciones ==")
with app.app_context():
    ruta = db.session.get(Ruta, rid)
    ruta.pedidos[0].estado = EstadoPedido.ENTREGADO
    pid_entregado = ruta.pedidos[0].id
    db.session.commit()
tok = token(c, f"/rutas/{rid}")
r = c.post(f"/rutas/{rid}/quitar/{pid_entregado}", data={"csrf_token":tok}, follow_redirects=True)
check(b"No se puede retirar una parada ya cerrada" in r.data, "no deja retirar una parada entregada")
tok = token(c, f"/rutas/{rid}")
r = c.post(f"/rutas/{rid}/eliminar", data={"csrf_token":tok}, follow_redirects=True)
check(b"ya tiene entregas registradas" in r.data, "no deja eliminar una ruta con entregas")

print("\n== 9. Validaciones ==")
tok = token(c, f"/rutas/nueva?fecha={hoy.isoformat()}")
r = c.post("/rutas/nueva", data={"csrf_token":tok,"fecha":hoy.isoformat(),
           "conductor_id":str(cond_id),"vehiculo_id":"0","estrategia":"DISTANCIA"}, follow_redirects=True)
check(b"Seleccione al menos un pedido" in r.data, "exige seleccionar pedidos")
tok = token(c, f"/rutas/nueva?fecha={hoy.isoformat()}")
r = c.post("/rutas/nueva", data={"csrf_token":tok,"fecha":hoy.isoformat(),
           "conductor_id":str(cond_id),"vehiculo_id":"0","estrategia":"DISTANCIA",
           "pedido_id":[str(ids[1])]}, follow_redirects=True)
check(b"ya fueron asignados a otra ruta" in r.data, "detecta pedidos ya asignados (evita doble asignacion)")
r = c.post("/rutas/nueva", data={"fecha":hoy.isoformat(),"conductor_id":str(cond_id),
           "vehiculo_id":"0","estrategia":"DISTANCIA","pedido_id":[str(ids[0])]}, follow_redirects=True)
check(b"creada con" not in r.data, "sin token CSRF no crea la ruta")

print("\n== 10. Control de acceso ==")
cc = sesion("conductor1@sgds.com","Conductor123*")
for url in ["/rutas/","/rutas/nueva",f"/rutas/{rid}"]:
    check(cc.get(url).status_code==403, f"conductor bloqueado en {url}")
check(app.test_client().get("/rutas/").status_code==302, "anonimo redirigido")

print("\n== 11. Eliminacion de ruta limpia ==")
tok = token(c, f"/rutas/nueva?fecha={hoy.isoformat()}")
r = c.post("/rutas/nueva", data={"csrf_token":tok,"fecha":hoy.isoformat(),
           "conductor_id":str(cond_id),"vehiculo_id":"0","estrategia":"DISTANCIA",
           "pedido_id":[str(ids[0])]}, follow_redirects=True)
with app.app_context():
    r2 = db.session.query(Ruta).order_by(Ruta.id.desc()).first()
    rid2 = r2.id
tok = token(c, f"/rutas/{rid2}")
r = c.post(f"/rutas/{rid2}/eliminar", data={"csrf_token":tok}, follow_redirects=True)
check(b"volvieron a pendientes" in r.data, "elimina la ruta")
with app.app_context():
    check(db.session.get(Ruta, rid2) is None, "la ruta desaparece")
    check(db.session.get(Pedido, ids[0]).estado==EstadoPedido.PENDIENTE, "sus pedidos vuelven a pendientes")

print("\n"+"="*55)
print("RESULTADO: " + ("TODAS LAS PRUEBAS PASARON" if not fallos else f"{len(fallos)} FALLAS"))
for f in fallos: print("   - "+f)
sys.exit(1 if fallos else 0)
