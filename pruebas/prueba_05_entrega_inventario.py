import pathlib
import re, sys
from datetime import date, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from run import app
from app.extensions import db
from app.models import (Pedido, PedidoItem, Producto, Ruta, Usuario, Rol, EstadoPedido,
                        EstadoRuta, EventoPedido, MovimientoInventario, PruebaEntrega, Vehiculo)

fallos=[]
def check(c,m):
    print(("  OK   " if c else "  FALLA")+f" {m}")
    if not c: fallos.append(m)
app.config["WTF_CSRF_ENABLED"]=True

# Base limpia en cada corrida, sea SQLite o MySQL.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _preparar import reiniciar_base
reiniciar_base()

def sesion(correo, clave):
    c = app.test_client()
    html = c.get("/auth/login").data.decode()
    tok = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html).group(1)
    c.post("/auth/login", data={"csrf_token":tok,"correo":correo,"contrasena":clave})
    return c
def tok(c, url):
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', c.get(url).data.decode())
    return m.group(1) if m else None

# ---- Escenario limpio: ruta propia para conductor2 ----
hoy = date.today()
with app.app_context():
    cond = db.session.query(Usuario).filter_by(correo="conductor2@sgds.com").first()
    desp = db.session.query(Usuario).filter_by(correo="despachador@sgds.com").first()
    pa = db.session.query(Producto).filter_by(sku="SKU-1001").first()
    pb = db.session.query(Producto).filter_by(sku="SKU-1002").first()
    stock_a0, stock_b0 = pa.stock_actual, pb.stock_actual
    pa_id, pb_id, cond_id = pa.id, pb.id, cond.id

    ruta = Ruta(codigo="RUT-TEST-01", fecha=hoy, estado=EstadoRuta.PLANIFICADA,
                conductor_id=cond.id, distancia_km=10, duracion_min=30)
    db.session.add(ruta); db.session.flush()
    rid = ruta.id
    pids=[]
    for i,(nom,cant_a,cant_b) in enumerate([("Cliente Uno",5,3),("Cliente Dos",4,0),("Cliente Tres",2,6)], start=1):
        p = Pedido(codigo=f"T4-{i:03d}", cliente_nombre=nom, direccion=f"Calle {i}",
                   latitud=4.65+i*0.01, longitud=-74.08, fecha_despacho=hoy,
                   estado=EstadoPedido.ASIGNADO, ruta_id=rid, orden_en_ruta=i,
                   creado_por_id=desp.id, ventana_fin=time(12,0))
        db.session.add(p); db.session.flush(); pids.append(p.id)
        db.session.add(PedidoItem(pedido_id=p.id, producto_id=pa_id, cantidad=cant_a))
        if cant_b: db.session.add(PedidoItem(pedido_id=p.id, producto_id=pb_id, cantidad=cant_b))
    db.session.commit()
print(f"\nEscenario: ruta con 3 paradas. Stock inicial SKU-1001={stock_a0}, SKU-1002={stock_b0}")

c = sesion("conductor2@sgds.com","Conductor123*")

print("\n== 1. Vista movil de la ruta ==")
html = c.get("/conductor/").data.decode()
check("RUT-TEST-01" in html, "muestra su ruta del dia")
check("Cliente Uno" in html and "Cliente Tres" in html, "lista las 3 paradas")
check("Iniciar ruta (3 parada" in html, "ofrece iniciar la ruta")
check("Historial" in html, "enlace al historial")

print("\n== 2. Iniciar ruta (RF4) ==")
t = tok(c, "/conductor/")
TOKEN_VALIDO = t
r = c.post(f"/conductor/ruta/{rid}/iniciar", data={"csrf_token":t}, follow_redirects=True)
check(b"Ruta iniciada: 3 parada" in r.data, "inicia las 3 paradas de una vez")
with app.app_context():
    ruta = db.session.get(Ruta, rid)
    check(all(p.estado==EstadoPedido.EN_RUTA for p in ruta.pedidos), "todas pasan a EN_RUTA")
    check(ruta.estado==EstadoRuta.EN_CURSO, f"la ruta pasa a EN_CURSO ({ruta.estado})")
    check(ruta.iniciada_en is not None, "registra la hora de inicio")
    check(db.session.get(Producto,pa_id).stock_actual==stock_a0, "iniciar NO toca el inventario")

print("\n== 3. Detalle de parada ==")
html = c.get(f"/conductor/parada/{pids[0]}").data.decode()
check("Cliente Uno" in html, "muestra el cliente")
check("Confirmar entrega" in html and "No se pudo entregar" in html, "ofrece ambas acciones")
check("Caja bebidas 12 und" in html, "lista la mercancia a entregar")
check("google.com/maps/dir" in html, "enlace al navegador GPS")
check("descuenta automaticamente la mercancia" in html, "advierte del efecto en inventario")

print("\n== 4. ENTREGA -> descuento de inventario (RF5) ==")
t = tok(c, f"/conductor/parada/{pids[0]}")
r = c.post(f"/conductor/parada/{pids[0]}/entregar", data={
    "csrf_token":t, "receptor_nombre":"Maria Lopez", "receptor_documento":"52123456",
    "observacion":"Entregado en porteria", "latitud":"4.6612","longitud":"-74.0821"},
    follow_redirects=True)
check(b"Se descontaron 8 unidad" in r.data, "informa las unidades descontadas (5+3)")
with app.app_context():
    p = db.session.get(Pedido, pids[0])
    check(p.estado==EstadoPedido.ENTREGADO, "el pedido queda ENTREGADO")
    check(p.inventario_descontado is True, "marca el inventario como descontado")
    check(db.session.get(Producto,pa_id).stock_actual==stock_a0-5, f"SKU-1001: {stock_a0} -> {stock_a0-5}")
    check(db.session.get(Producto,pb_id).stock_actual==stock_b0-3, f"SKU-1002: {stock_b0} -> {stock_b0-3}")
    movs = db.session.query(MovimientoInventario).filter_by(pedido_id=pids[0]).all()
    check(len(movs)==2, f"crea 2 movimientos de SALIDA ({len(movs)})")
    check(all(m.tipo=="SALIDA" and p.codigo in m.motivo for m in movs), "los movimientos citan el pedido")
    check(all(m.usuario_id==cond_id for m in movs), "el movimiento queda a nombre del conductor")
    pod = db.session.get(Pedido,pids[0]).prueba_entrega
    check(pod is not None and pod.receptor_nombre=="Maria Lopez", "guarda la prueba de entrega (PoD)")
    check(pod.receptor_documento=="52123456" and pod.latitud==4.6612, "PoD con documento y geolocalizacion")
    ev = db.session.query(EventoPedido).filter_by(pedido_id=pids[0], estado_nuevo=EstadoPedido.ENTREGADO).first()
    check(ev is not None and ev.latitud==4.6612, "evento con coordenadas del terreno")

print("\n== 5. Idempotencia: reintento no descuenta dos veces ==")
with app.app_context():
    stock_antes = db.session.get(Producto,pa_id).stock_actual
# La parada ya entregada no muestra formularios, asi que se toma un token valido
# de otra parada abierta (los tokens CSRF son por sesion, no por formulario).
t = tok(c, f"/conductor/parada/{pids[2]}")
check(t is not None, "obtiene un token CSRF valido de la sesion")
r = c.post(f"/conductor/parada/{pids[0]}/entregar", data={"csrf_token":t,"receptor_nombre":"Otro"}, follow_redirects=True)
check(b"ya se encuentra en estado Entregado" in r.data, "rechaza la segunda entrega")
with app.app_context():
    check(db.session.get(Producto,pa_id).stock_actual==stock_antes, "el stock NO se descuenta de nuevo")
    check(db.session.query(MovimientoInventario).filter_by(pedido_id=pids[0]).count()==2, "no se duplican movimientos")

print("\n== 6. FALLIDO -> no afecta inventario ==")
with app.app_context():
    stock_antes = db.session.get(Producto,pa_id).stock_actual
t = tok(c, f"/conductor/parada/{pids[1]}")
r = c.post(f"/conductor/parada/{pids[1]}/fallar", data={
    "csrf_token":t,"motivo_fallo":"Cliente ausente","observacion":"Timbre sin respuesta"}, follow_redirects=True)
check(b"registrado como fallido" in r.data and b"inventario no se afecto" in r.data, "registra el fallo")
with app.app_context():
    p = db.session.get(Pedido, pids[1])
    check(p.estado==EstadoPedido.FALLIDO, "queda FALLIDO")
    check(p.inventario_descontado is False, "no marca inventario descontado")
    check(db.session.get(Producto,pa_id).stock_actual==stock_antes, "el stock queda intacto")
    check(db.session.query(MovimientoInventario).filter_by(pedido_id=pids[1]).count()==0, "sin movimientos de inventario")
    check(p.prueba_entrega.motivo_fallo=="Cliente ausente", "guarda el motivo del fallo")

print("\n== 7. Motivo obligatorio en el fallo ==")
t = tok(c, f"/conductor/parada/{pids[2]}")
r = c.post(f"/conductor/parada/{pids[2]}/fallar", data={"csrf_token":t,"motivo_fallo":""}, follow_redirects=True)
check(b"Indique el motivo" in r.data, "exige el motivo")
with app.app_context():
    check(db.session.get(Pedido,pids[2]).estado==EstadoPedido.EN_RUTA, "el pedido no cambia de estado")

print("\n== 8. Reintento de una entrega fallida ==")
t = tok(c, f"/conductor/parada/{pids[1]}")
r = c.post(f"/conductor/parada/{pids[1]}/reintentar", data={"csrf_token":t}, follow_redirects=True)
check(b"en reintento de entrega" in r.data, "permite reintentar")
with app.app_context():
    check(db.session.get(Pedido,pids[1]).estado==EstadoPedido.EN_RUTA, "vuelve a EN_RUTA")
t = tok(c, f"/conductor/parada/{pids[1]}")
with app.app_context():
    stock_antes = db.session.get(Producto,pa_id).stock_actual
r = c.post(f"/conductor/parada/{pids[1]}/entregar", data={"csrf_token":t,"receptor_nombre":"Segundo intento"}, follow_redirects=True)
check(b"Se descontaron 4 unidad" in r.data, "el reintento exitoso si descuenta")
with app.app_context():
    check(db.session.get(Producto,pa_id).stock_actual==stock_antes-4, "descuenta las 4 unidades")
    check(db.session.get(Pedido,pids[1]).prueba_entrega.motivo_fallo is None, "limpia el motivo de fallo previo")

print("\n== 9. Cierre automatico de la ruta ==")
with app.app_context():
    check(db.session.get(Ruta,rid).estado==EstadoRuta.EN_CURSO, "con paradas abiertas sigue EN_CURSO")
t = tok(c, f"/conductor/parada/{pids[2]}")
r = c.post(f"/conductor/parada/{pids[2]}/entregar", data={"csrf_token":t,"receptor_nombre":"Ultimo"}, follow_redirects=True)
with app.app_context():
    ruta = db.session.get(Ruta, rid)
    check(ruta.estado==EstadoRuta.FINALIZADA, f"al cerrar la ultima parada la ruta FINALIZA ({ruta.estado})")
    check(ruta.finalizada_en is not None, "registra la hora de finalizacion")
    check(ruta.avance_porcentaje==100.0, f"avance 100% ({ruta.avance_porcentaje})")

print("\n== 9b. Una ruta se puede finalizar con pedidos cancelados ==")
with app.app_context():
    from app.services.despacho import anular_pedido

    desp3 = db.session.query(Usuario).filter_by(correo="despachador@sgds.com").first()
    ruta3 = Ruta(codigo="RUT-TEST-03", fecha=hoy, estado=EstadoRuta.PLANIFICADA, conductor_id=cond_id)
    db.session.add(ruta3); db.session.flush()
    p_entregar = Pedido(codigo="T4-CANC-01", cliente_nombre="Cliente Cancela Uno", direccion="Calle Y1",
                        latitud=4.66, longitud=-74.08, fecha_despacho=hoy,
                        estado=EstadoPedido.ASIGNADO, ruta_id=ruta3.id, orden_en_ruta=1,
                        creado_por_id=desp3.id)
    p_cancelar = Pedido(codigo="T4-CANC-02", cliente_nombre="Cliente Cancela Dos", direccion="Calle Y2",
                        fecha_despacho=hoy, estado=EstadoPedido.ASIGNADO, ruta_id=ruta3.id, orden_en_ruta=2,
                        creado_por_id=desp3.id)
    db.session.add_all([p_entregar, p_cancelar]); db.session.flush()
    ruta3_id, id_entregar, id_cancelar = ruta3.id, p_entregar.id, p_cancelar.id
    anular_pedido(p_cancelar, desp3.id, "Cliente cancelo el pedido")
    db.session.commit()
    check(db.session.get(Ruta, ruta3_id).estado != EstadoRuta.FINALIZADA,
          "con una parada aun asignada la ruta no finaliza")

t = tok(c, f"/conductor/parada/{id_entregar}")
c.post(f"/conductor/parada/{id_entregar}/en-ruta", data={"csrf_token": t}, follow_redirects=True)
t = tok(c, f"/conductor/parada/{id_entregar}")
c.post(f"/conductor/parada/{id_entregar}/entregar",
       data={"csrf_token": t, "receptor_nombre": "Alguien"}, follow_redirects=True)
with app.app_context():
    ruta3 = db.session.get(Ruta, ruta3_id)
    check(ruta3.estado == EstadoRuta.FINALIZADA,
          f"la ruta finaliza aunque tenga una parada CANCELADA ({ruta3.estado})")
    check(db.session.get(Pedido, id_cancelar).estado == EstadoPedido.CANCELADO,
          "el pedido cancelado se conserva en la ruta, no se elimina")

print("\n== 10. Transiciones invalidas ==")
with app.app_context():
    from flask_wtf.csrf import generate_csrf
t = TOKEN_VALIDO
r = c.post(f"/conductor/parada/{pids[0]}/en-ruta", data={"csrf_token":t}, follow_redirects=True)
check(b"No es posible pasar de Entregado" in r.data or b"ya se encuentra" in r.data, "no deja revertir una entrega")
with app.app_context():
    check(db.session.get(Pedido,pids[0]).estado==EstadoPedido.ENTREGADO, "el estado no cambia")

print("\n== 11. Aislamiento entre conductores (RNF5) ==")
c1 = sesion("conductor1@sgds.com","Conductor123*")
check(c1.get(f"/conductor/parada/{pids[0]}").status_code==403, "no puede ver la parada de otro conductor")
t1 = tok(c1, "/conductor/")
r = c1.post(f"/conductor/parada/{pids[2]}/entregar", data={"csrf_token":t1,"receptor_nombre":"Intruso"}, follow_redirects=False)
check(r.status_code==403, "no puede operar sobre la parada de otro")
r = c1.post(f"/conductor/ruta/{rid}/iniciar", data={"csrf_token":t1}, follow_redirects=False)
check(r.status_code==403, "no puede iniciar la ruta de otro")

print("\n== 12. CSRF y roles ==")
with app.app_context():
    stock_csrf = db.session.get(Producto,pa_id).stock_actual
r = c.post(f"/conductor/parada/{pids[2]}/entregar", data={"receptor_nombre":"Sin token"}, follow_redirects=True)
check(r.status_code==400 and b"La sesion expiro" in r.data, "sin token CSRF muestra pagina en espanol")
check(b"CSRF token" not in r.data and b"Bad Request" not in r.data, "no filtra el error crudo en ingles")
with app.app_context():
    check(db.session.get(Producto,pa_id).stock_actual==stock_csrf, "sin token CSRF el inventario no se toca")
ca = sesion("admin@sgds.com","Admin123*")
check(ca.get("/conductor/").status_code==403, "el admin no entra a la vista de conductor")
check(app.test_client().get("/conductor/").status_code==302, "anonimo redirigido")

print("\n== 13. Descuadre de inventario (stock insuficiente) ==")
with app.app_context():
    desp2 = db.session.query(Usuario).filter_by(correo="despachador@sgds.com").first()
    prod = Producto(sku="SKU-ESCASO", nombre="Producto escaso", stock_actual=2, stock_minimo=0)
    db.session.add(prod); db.session.flush()
    ruta2 = Ruta(codigo="RUT-TEST-02", fecha=hoy, estado=EstadoRuta.PLANIFICADA, conductor_id=cond_id)
    db.session.add(ruta2); db.session.flush()
    p = Pedido(codigo="T4-ESCASO", cliente_nombre="Cliente Escaso", direccion="Calle X",
               fecha_despacho=hoy, estado=EstadoPedido.ASIGNADO, ruta_id=ruta2.id, orden_en_ruta=1,
               creado_por_id=desp2.id)
    db.session.add(p); db.session.flush()
    db.session.add(PedidoItem(pedido_id=p.id, producto_id=prod.id, cantidad=10))
    db.session.commit()
    esc_pid, esc_prod = p.id, prod.id
t = tok(c, f"/conductor/parada/{esc_pid}")
c.post(f"/conductor/parada/{esc_pid}/en-ruta", data={"csrf_token":t}, follow_redirects=True)
t = tok(c, f"/conductor/parada/{esc_pid}")
r = c.post(f"/conductor/parada/{esc_pid}/entregar", data={"csrf_token":t,"receptor_nombre":"Quien sea"}, follow_redirects=True)
check(b"Descuadre de inventario" in r.data, "detecta y reporta el descuadre")
with app.app_context():
    check(db.session.get(Pedido,esc_pid).estado==EstadoPedido.ENTREGADO, "la entrega NO se bloquea (la mercancia ya salio)")
    check(db.session.get(Producto,esc_prod).stock_actual==-8, f"el stock refleja el faltante real ({db.session.get(Producto,esc_prod).stock_actual})")

print("\n== 13b. with_for_update evita actualizaciones perdidas de inventario ==")
with app.app_context():
    from app.services.despacho import TransicionInvalida, cambiar_estado

    producto = db.session.query(Producto).filter_by(sku="SKU-1003").first()
    prod_conc_id, stock_original = producto.id, producto.stock_actual

    desp_conc = db.session.query(Usuario).filter_by(correo="despachador@sgds.com").first()
    cond_conc = db.session.query(Usuario).filter_by(correo="conductor2@sgds.com").first()
    ruta_conc = Ruta(codigo="RUT-CONC-01", fecha=hoy, estado=EstadoRuta.PLANIFICADA,
                     conductor_id=cond_conc.id)
    db.session.add(ruta_conc); db.session.flush()
    p_conc = Pedido(codigo="T5-CONC-01", cliente_nombre="Cliente Concurrencia",
                    direccion="Calle Z", fecha_despacho=hoy, estado=EstadoPedido.EN_RUTA,
                    ruta_id=ruta_conc.id, orden_en_ruta=1, creado_por_id=desp_conc.id)
    db.session.add(p_conc); db.session.flush()
    db.session.add(PedidoItem(pedido_id=p_conc.id, producto_id=prod_conc_id, cantidad=3))
    db.session.commit()
    pid_conc = p_conc.id

    # `producto` sigue en el identity map de esta sesion con stock_original
    # cacheado. Se simula otra transaccion concurrente que ya modifico el
    # stock por su cuenta (otro despacho), escribiendo directo con una
    # conexion aparte para no tocar la sesion ni su cache.
    with db.engine.begin() as conexion:
        conexion.execute(
            Producto.__table__.update()
            .where(Producto.__table__.c.id == prod_conc_id)
            .values(stock_actual=stock_original - 50)
        )

    pedido_conc = db.session.get(Pedido, pid_conc)
    cambiar_estado(pedido_conc, EstadoPedido.ENTREGADO, cond_conc.id, nota="Prueba de concurrencia")
    db.session.commit()

    esperado = (stock_original - 50) - 3
    resultado = db.session.get(Producto, prod_conc_id).stock_actual
    check(
        resultado == esperado,
        f"el descuento parte del stock real en la BD, no del cacheado en Python "
        f"({resultado} vs esperado {esperado}; sin with_for_update habria dado {stock_original - 3})",
    )

    # El pedido tambien se relee con la fila bloqueada: si otra peticion ya lo
    # transiciono por su cuenta, la validacion debe basarse en ese estado real.
    p_estado = Pedido(codigo="T5-CONC-02", cliente_nombre="Cliente Concurrencia 2",
                      direccion="Calle Z2", fecha_despacho=hoy, estado=EstadoPedido.ASIGNADO,
                      ruta_id=ruta_conc.id, orden_en_ruta=2, creado_por_id=desp_conc.id)
    db.session.add(p_estado); db.session.commit()
    pid_estado = p_estado.id

    pedido_obj = db.session.query(Pedido).filter_by(id=pid_estado).first()  # cachea estado ASIGNADO
    with db.engine.begin() as conexion:
        conexion.execute(
            Pedido.__table__.update().where(Pedido.__table__.c.id == pid_estado)
            .values(estado=EstadoPedido.EN_RUTA)
        )
    try:
        cambiar_estado(pedido_obj, EstadoPedido.EN_RUTA, cond_conc.id)
        rechazo = False
    except TransicionInvalida:
        rechazo = True
    check(rechazo, "cambiar_estado valida el estado real en BD, no el que tenia cacheado en Python")

print("\n== 14. Historial ==")
html = c.get("/conductor/historial").data.decode()
check("RUT-TEST-01" in html, "lista las rutas del conductor")
check("100" in html, "muestra el avance")

print("\n== 15. El tablero refleja la operacion (RF6) ==")
cd = sesion("despachador@sgds.com","Despacho123*")
html = cd.get("/admin/", follow_redirects=True).data.decode()
kpis = re.findall(r'kpi-valor">([^<]+)<', html)
check(len(kpis)==3, f"los 3 KPIs siguen presentes: {kpis}")
html2 = cd.get(f"/pedidos/{pids[0]}").data.decode()
check("Maria Lopez" in html2, "el admin ve la prueba de entrega del conductor")
check("Inventario descontado" in html2, "el detalle confirma el descuento")

print("\n"+"="*55)
print("RESULTADO: " + ("TODAS LAS PRUEBAS PASARON" if not fallos else f"{len(fallos)} FALLAS"))
for f in fallos: print("   - "+f)
sys.exit(1 if fallos else 0)
