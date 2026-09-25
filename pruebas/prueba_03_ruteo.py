import pathlib
import json, sys
from datetime import date, time
from unittest.mock import patch
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import requests
from run import app
from app.services import ruteo

fallos=[]
def check(c,m):
    print(("  OK   " if c else "  FALLA")+f" {m}")
    if not c: fallos.append(m)

class P:
    """Pedido simulado."""
    def __init__(self, id, lat, lng, ventana_fin=None, prioridad=3):
        self.id, self.latitud, self.longitud = id, lat, lng
        self.ventana_fin, self.prioridad = ventana_fin, prioridad

ORIGEN = (4.6482837, -74.2478938)   # CD en Mosquera/Bogota occidente
# Destinos reales de Bogota
PEDIDOS = [
    P(1, 4.7014, -74.0713, time(14,0)),   # Suba (norte)
    P(2, 4.6285, -74.1465, time(17,0)),   # Kennedy (sur-occidente, mas cerca del CD)
    P(3, 4.6412, -74.0637, time(9,0), 1), # Chapinero (oriente, ventana temprana)
]

print("\n== 1. Haversine ==")
d = ruteo.distancia_haversine(4.6482837,-74.2478938, 4.6412,-74.0637)
check(19 < d < 22, f"distancia CD->Chapinero = {d:.1f} km (esperado ~20)")
check(ruteo.distancia_haversine(4.6,-74.0,4.6,-74.0) == 0, "distancia a si mismo = 0")

print("\n== 2. Vecino mas cercano (respaldo local) ==")
orden, dist = ruteo._vecino_mas_cercano(ORIGEN, PEDIDOS)
check(orden[0].id == 2, f"arranca por el mas cercano al CD (Kennedy=2), obtuvo {orden[0].id}")
check(len(orden) == 3 and dist > 0, f"recorre las 3 paradas, {dist:.1f} km")

print("\n== 3. Ordenamiento por ventana horaria ==")
orden = ruteo._ordenar_por_ventana(PEDIDOS)
check([p.id for p in orden] == [3,1,2], f"ordena por hora limite: {[p.id for p in orden]} (esperado [3,1,2])")
mixto = PEDIDOS + [P(4, 4.65,-74.10, None, 1)]
orden = ruteo._ordenar_por_ventana(mixto)
check(orden[-1].id == 4, "los pedidos sin ventana quedan al final")

print("\n== 4. OSRM en vivo (optimizacion por distancia) ==")
with app.app_context():
    r = ruteo.calcular_ruta(ORIGEN, PEDIDOS, ruteo.ESTRATEGIA_DISTANCIA)
check(r.proveedor == "OSRM", f"uso OSRM (proveedor={r.proveedor})")
check(len(r.orden) == 3, f"secuencia completa: {r.orden}")
check(r.distancia_km > 0 and r.duracion_min > 0, f"{r.distancia_km} km / {r.duracion_min} min")
check(r.geometria is not None, "devuelve geometria para el mapa")
if r.geometria:
    g = json.loads(r.geometria)
    check(len(g) > 10 and len(g[0]) == 2, f"geometria con {len(g)} puntos [lat,lng]")
    check(-5 < g[0][0] < 15 and -80 < g[0][1] < -70, f"coordenadas en orden [lat,lng]: {g[0]}")
check(not r.advertencias, f"sin advertencias: {r.advertencias}")

print("\n== 5. OSRM en vivo (orden fijo por ventana) ==")
with app.app_context():
    r2 = ruteo.calcular_ruta(ORIGEN, PEDIDOS, ruteo.ESTRATEGIA_VENTANA)
check(r2.orden == [3,1,2], f"respeta el orden por ventana: {r2.orden}")
check(r2.proveedor == "OSRM" and r2.distancia_km > 0, f"calcula distancia real: {r2.distancia_km} km")
check(r2.distancia_km >= r.distancia_km, f"la ruta por ventana no es mas corta que la optimizada ({r2.distancia_km} vs {r.distancia_km} km)")

print("\n== 6. Degradacion sin conexion ==")
with patch("app.services.ruteo.requests.get", side_effect=requests.ConnectionError("sin red")):
    with app.app_context():
        rc = ruteo.calcular_ruta(ORIGEN, PEDIDOS, ruteo.ESTRATEGIA_DISTANCIA)
check(rc.proveedor == "LOCAL", "degrada al algoritmo local")
check(rc.uso_respaldo, "marca que uso respaldo")
check(len(rc.orden) == 3 and rc.distancia_km > 0, f"igual entrega una ruta: {rc.orden}, {rc.distancia_km} km")
check(any("no se pudo contactar" in a.lower() for a in rc.advertencias), "advierte al usuario")
check(rc.geometria is None, "sin geometria (se dibujara linea recta)")

with patch("app.services.ruteo.requests.get", side_effect=requests.Timeout("lento")):
    with app.app_context():
        rt = ruteo.calcular_ruta(ORIGEN, PEDIDOS, ruteo.ESTRATEGIA_VENTANA)
check(rt.proveedor == "LOCAL" and rt.orden == [3,1,2], "timeout: conserva el orden por ventana")

print("\n== 7. Respuesta corrupta del proveedor ==")
class RespFalsa:
    status_code=200
    def raise_for_status(self): pass
    def json(self): return {"code":"NoRoute","message":"sin ruta"}
with patch("app.services.ruteo.requests.get", return_value=RespFalsa()):
    with app.app_context():
        rn = ruteo.calcular_ruta(ORIGEN, PEDIDOS)
check(rn.proveedor == "LOCAL" and len(rn.orden)==3, "OSRM sin ruta -> respaldo local")

print("\n== 8. Pedidos sin coordenadas ==")
mezcla = PEDIDOS + [P(9, None, None)]
with app.app_context():
    rm = ruteo.calcular_ruta(ORIGEN, mezcla)
check(rm.orden[-1] == 9, f"el pedido sin geo queda al final: {rm.orden}")
check(len(rm.orden) == 4, "no se pierde ningun pedido")
check(any("sin coordenadas" in a for a in rm.advertencias), "advierte sobre los no geolocalizados")

with app.app_context():
    rv = ruteo.calcular_ruta(ORIGEN, [P(9,None,None), P(8,None,None)])
check(rv.distancia_km == 0 and len(rv.orden)==2, "ningun pedido geolocalizado: no falla")
check(any("no fue posible trazar" in a.lower() for a in rv.advertencias), "explica por que no hay ruta")

print("\n== 9. Caso de una sola parada ==")
with app.app_context():
    r1 = ruteo.calcular_ruta(ORIGEN, [PEDIDOS[0]])
check(len(r1.orden)==1 and r1.distancia_km > 0, f"una parada: {r1.distancia_km} km, proveedor {r1.proveedor}")

print("\n== 10. En produccion se desactiva el reintento por HTTP a OSRM ==")
import os
with app.app_context():
    with patch.dict(os.environ, {"ENTORNO": "produccion"}):
        bases_prod = ruteo._base_osrm()
    with patch.dict(os.environ, {"ENTORNO": "desarrollo"}):
        bases_dev = ruteo._base_osrm()
check(bases_prod == [ruteo.URL_OSRM_SEGURA], f"produccion solo intenta HTTPS ({bases_prod})")
check(
    bases_dev == [ruteo.URL_OSRM_SEGURA, ruteo.URL_OSRM_PLANA],
    f"fuera de produccion conserva el reintento por HTTP ({bases_dev})",
)

print("\n"+"="*55)
print("RESULTADO: " + ("TODAS LAS PRUEBAS PASARON" if not fallos else f"{len(fallos)} FALLAS"))
for f in fallos: print("   - "+f)
sys.exit(1 if fallos else 0)
