import pathlib
import re, sys, subprocess, os
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
fallos=[]
def check(c,m):
    print(("  OK   " if c else "  FALLA")+f" {m}")
    if not c: fallos.append(m)

# Base limpia en cada corrida, sea SQLite o MySQL.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _preparar import reiniciar_base
reiniciar_base()

from run import app
from app.extensions import db
from app.services import analitica
from app.models import (EstadoPedido, MedicionRendimiento, Pedido, PruebaEntrega,
                        UMBRAL_MAXIMO_MS, Usuario)
app.config["WTF_CSRF_ENABLED"]=True

def sesion(correo, clave):
    c = app.test_client()
    h = c.get("/auth/login").data.decode()
    t = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', h).group(1)
    c.post("/auth/login", data={"csrf_token":t,"correo":correo,"contrasena":clave})
    return c

print("\n== 1. Servicio de analitica ==")
with app.app_context():
    serie = analitica.serie_entregas(14)
    check(len(serie)==14, f"serie de 14 dias ({len(serie)})")
    check(all("entregadas" in p and "fallidas" in p for p in serie), "cada punto trae ambas series")
    total = sum(p["total"] for p in serie)
    check(total > 0, f"hay {total} entregas cerradas en 14 dias")
    fechas = [p["fecha"] for p in serie]
    check(fechas == sorted(fechas), "serie ordenada cronologicamente")
    check(len(set(fechas))==14, "sin fechas repetidas ni huecos")

    motivos = analitica.motivos_fallo(30)
    check(len(motivos)>0, f"{len(motivos)} motivos de fallo distintos")
    cants = [m["cantidad"] for m in motivos]
    check(cants == sorted(cants, reverse=True), f"ordenados de mayor a menor: {cants}")

    cond = analitica.productividad_conductores(30)
    check(len(cond)==2, f"2 conductores con operacion ({len(cond)})")
    check(all(0 <= c["exito"] <= 100 for c in cond), "tasa de exito en rango valido")
    check(all(c["cerradas"]==c["entregadas"]+c["fallidas"] for c in cond), "las cifras cuadran")

    t = analitica.tiempo_promedio_entrega(30)
    check(t["promedio_min"] is not None and t["muestras"]>0, f"tiempo promedio {t['promedio_min']} min sobre {t['muestras']} entregas")
    check(9 <= t["minimo"] <= t["promedio_min"] <= t["maximo"] <= 40, f"rango coherente: {t['minimo']}–{t['maximo']} min")

    v = analitica.cumplimiento_ventana(30)
    check(v["porcentaje"] is not None and 0 <= v["porcentaje"] <= 100, f"cumplimiento de ventana {v['porcentaje']}%")
    check(v["dentro"] <= v["total"], "los cumplidos no superan el total")

print("\n== 2. Periodos vacios no rompen nada ==")
with app.app_context():
    from datetime import date, timedelta
    lejos = date.today() - timedelta(days=400)
    s2 = analitica.serie_entregas(7, hasta=lejos)
    check(len(s2)==7 and sum(p["total"] for p in s2)==0, "serie vacia sin error")
    t2 = analitica.tiempo_promedio_entrega(7, hasta=lejos)
    check(t2["promedio_min"] is None and t2["muestras"]==0, "tiempo promedio sin datos devuelve None")
    v2 = analitica.cumplimiento_ventana(7, hasta=lejos)
    check(v2["porcentaje"] is None, "cumplimiento sin datos devuelve None")
    check(analitica.motivos_fallo(7, hasta=lejos)==[], "motivos vacio")

print("\n== 3. Vista de analitica ==")
c = sesion("despachador@sgds.com","Despacho123*")
r = c.get("/admin/analitica")
check(r.status_code==200, "carga la pagina")
html = r.data.decode()
check("Tiempo promedio de entrega" in html, "muestra el tiempo promedio")
check("Cumplimiento de ventana horaria" in html, "muestra cumplimiento de ventana")
check("Causas de entrega fallida" in html, "muestra causas de fallo")
check("Entregas cerradas por conductor" in html, "muestra productividad")
check(html.count("data-tooltip") > 10, f"marcas con tooltip: {html.count('data-tooltip')}")
check("class=\"leyenda\"" in html, "leyenda presente para las 2 series")
check("Ver los datos en tabla" in html, "vista de tabla alterna disponible")
check("segmento-entregadas" in html and "segmento-fallidas" in html, "ambas series renderizadas")
check("#0ca30c" not in html and "#d03b3b" not in html.split("marca-umbral")[0], "no usa el par verde/rojo para las series")
for dias in (7,14,30):
    check(c.get(f"/admin/analitica?dias={dias}").status_code==200, f"rango de {dias} dias")
check(c.get("/admin/analitica?dias=999").status_code==200, "rango invalido no rompe")

print("\n== 4. Instrumentacion RNF2 ==")
with app.app_context():
    antes = db.session.query(MedicionRendimiento).count()
c.get("/admin/analitica")
with app.app_context():
    check(db.session.query(MedicionRendimiento).count() > antes, "registra la medicion de cada peticion")
    ult = db.session.query(MedicionRendimiento).order_by(MedicionRendimiento.id.desc()).first()
    check(ult.duracion_ms > 0, f"duracion registrada: {ult.duracion_ms:.1f} ms")
    check(ult.estado_http == 200, "guarda el codigo de estado")
r = c.get("/admin/analitica")
check("X-Tiempo-Respuesta-ms" in r.headers, "expone el tiempo en la cabecera HTTP")
check(float(r.headers["X-Tiempo-Respuesta-ms"]) > 0, f"cabecera con valor: {r.headers.get('X-Tiempo-Respuesta-ms')} ms")
with app.app_context():
    estaticos = db.session.query(MedicionRendimiento).filter_by(endpoint="static").count()
    check(estaticos == 0, "no mide peticiones de archivos estaticos")

print("\n== 5. Panel de rendimiento ==")
ca = sesion("admin@sgds.com","Admin123*")
# Generar trafico en transacciones criticas
for _ in range(12):
    ca.get("/admin/")
cc = sesion("conductor1@sgds.com","Conductor123*")
for _ in range(8):
    cc.get("/conductor/")
r = ca.get("/admin/rendimiento")
check(r.status_code==200, "carga el panel")
html = r.data.decode()
check("Cumplimiento del RNF2" in html, "muestra el cumplimiento")
check("Percentil 95" in html, "muestra el p95")
check("marca-umbral" in html, "dibuja la linea del umbral de 5 s")
check("Tablero del administrador" in html, "nombra las transacciones criticas en espanol")
m = re.search(r'kpi-valor">([\d.]+)<small class="kpi-unidad">%', html)
check(m is not None, "extrae el porcentaje de cumplimiento")
if m:
    check(float(m.group(1)) == 100.0, f"cumplimiento del RNF2 = {m.group(1)}% (todas bajo 5 s)")
with app.app_context():
    d = analitica.rendimiento(24)
    check(d["maximo_ms"] < UMBRAL_MAXIMO_MS, f"peor tiempo observado {d['maximo_ms']} ms < {UMBRAL_MAXIMO_MS} ms")
    print(f"       mediana {d['mediana_ms']} ms · p95 {d['p95_ms']} ms · maximo {d['maximo_ms']} ms sobre {d['muestras']} muestras")

print("\n== 6. Control de acceso a los nuevos paneles ==")
check(cc.get("/admin/analitica").status_code==403, "conductor bloqueado en analitica")
check(cc.get("/admin/rendimiento").status_code==403, "conductor bloqueado en rendimiento")
check(c.get("/admin/rendimiento").status_code==403, "el despachador no ve rendimiento (solo admin)")
check(c.get("/admin/analitica").status_code==200, "el despachador si ve analitica")
check(app.test_client().get("/admin/analitica").status_code==302, "anonimo redirigido")

print("\n== 7. La telemetria no rompe la operacion ==")
r = ca.get("/ruta-que-no-existe")
check(r.status_code==404, "una URL inexistente sigue devolviendo 404")
with app.app_context():
    sin_ruta = db.session.query(MedicionRendimiento).filter(
        MedicionRendimiento.endpoint.is_(None)).count()
    check(sin_ruta == 0, "una URL sin ruta definida no genera medicion (no hay transaccion)")
r = ca.get("/pedidos/999999")
check(r.status_code==404, "abort(404) desde una vista real")
with app.app_context():
    medido = db.session.query(MedicionRendimiento).filter_by(
        endpoint="pedidos.detalle", estado_http=404).count()
    check(medido > 0, "las respuestas de error de un endpoint real si se miden")
r = ca.get("/conductor/")
check(r.status_code==403, "un 403 sigue siendo 403")
with app.app_context():
    check(db.session.query(MedicionRendimiento).filter_by(estado_http=403).count() > 0,
          "tambien mide las respuestas 403")

print("\n== 8. Zona horaria America/Bogota ==")
with app.app_context():
    from datetime import datetime as dt_clase, time as time_clase, timedelta as td
    from zoneinfo import ZoneInfo
    from app.tiempo import ZONA_BOGOTA, ahora, hoy

    # Colombia no observa horario de verano: el desplazamiento es siempre -5 horas.
    instante_utc = dt_clase(2026, 6, 15, 15, 30, tzinfo=ZoneInfo("UTC"))
    en_bogota = instante_utc.astimezone(ZONA_BOGOTA).replace(tzinfo=None)
    check(en_bogota == dt_clase(2026, 6, 15, 10, 30), f"15:30 UTC son las 10:30 en Bogota ({en_bogota})")
    check(hoy() == ahora().date(), "hoy() es la fecha de ahora() en Bogota")

    # Una entrega a las 10:30 (hora Bogota) debe contar como dentro de una
    # ventana que cierra a las 11:00, sin importar la zona horaria del servidor.
    dia_prueba = hoy() - td(days=250)
    despachador = db.session.query(Usuario).filter_by(correo="despachador@sgds.com").first()
    pedido = Pedido(
        codigo="TZ-TEST-001", cliente_nombre="Cliente Zona Horaria",
        direccion="Calle Prueba", fecha_despacho=dia_prueba,
        estado=EstadoPedido.ENTREGADO, ventana_fin=time_clase(11, 0),
        creado_por_id=despachador.id,
    )
    db.session.add(pedido)
    db.session.flush()
    db.session.add(PruebaEntrega(
        pedido_id=pedido.id, receptor_nombre="Receptor de prueba",
        registrado_en=dt_clase(dia_prueba.year, dia_prueba.month, dia_prueba.day, 10, 30),
    ))
    db.session.commit()

    resultado = analitica.cumplimiento_ventana(dias=1, hasta=dia_prueba)
    check(
        resultado["total"] == 1 and resultado["dentro"] == 1,
        f"entrega a las 10:30 (hora Bogota) dentro de la ventana que cierra a las 11:00 ({resultado})",
    )

print("\n"+"="*55)
print("RESULTADO: " + ("TODAS LAS PRUEBAS PASARON" if not fallos else f"{len(fallos)} FALLAS"))
for f in fallos: print("   - "+f)
sys.exit(1 if fallos else 0)
