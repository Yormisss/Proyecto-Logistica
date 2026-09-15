"""RF3 - Generacion de Rutas Basicas.

Integra una API de geolocalizacion externa para trazar la ruta secuencial
sugerida al conductor, evitando el desarrollo de un algoritmo de enrutamiento
desde cero (RF3).

Proveedor principal: OSRM (Open Source Routing Machine), servicio publico que no
requiere clave de API. El servicio `/trip` resuelve el ordenamiento de paradas
minimizando la distancia total; el servicio `/route` calcula la geometria real
cuando el orden ya viene impuesto por otro criterio.

Ante cualquier fallo de red o del proveedor, el sistema degrada a un algoritmo
local de vecino mas cercano sobre distancia haversine. Esto responde a la
limitacion de "brechas de conectividad urbana" del numeral 1.4: la planificacion
nunca queda bloqueada por la ausencia de conexion.
"""

import json
import math

import requests

TIEMPO_ESPERA = 20          # segundos
RADIO_TIERRA_KM = 6371.0

# El servidor publico de OSRM exige un handshake TLS que el interprete de Python
# del sistema (compilado contra LibreSSL 2.8.3) no puede negociar. Se intenta
# primero por HTTPS y, ante un fallo estrictamente de SSL, se reintenta por HTTP.
# En un despliegue sobre un Python con OpenSSL moderno el primer intento tiene
# exito y nunca se recurre al canal sin cifrar.
URL_OSRM_SEGURA = "https://router.project-osrm.org"
URL_OSRM_PLANA = "http://router.project-osrm.org"

# Estrategias de ordenamiento disponibles para el gestor logistico.
ESTRATEGIA_DISTANCIA = "DISTANCIA"
ESTRATEGIA_VENTANA = "VENTANA"

ESTRATEGIAS = {
    ESTRATEGIA_DISTANCIA: "Optimizar distancia total (recomendada)",
    ESTRATEGIA_VENTANA: "Respetar ventanas horarias del cliente",
}


class ResultadoRuteo:
    """Resultado del calculo de una ruta."""

    def __init__(self, orden, distancia_km, duracion_min, geometria, proveedor, advertencias=None):
        self.orden = orden                  # Lista de ids de pedido, ya secuenciada
        self.distancia_km = distancia_km
        self.duracion_min = duracion_min
        self.geometria = geometria          # JSON: [[lat, lng], ...] o None
        self.proveedor = proveedor          # OSRM | LOCAL
        self.advertencias = advertencias or []

    @property
    def uso_respaldo(self):
        return self.proveedor == "LOCAL"


def distancia_haversine(lat1, lng1, lat2, lng2):
    """Distancia en km entre dos coordenadas sobre la superficie terrestre."""
    d_lat = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lng / 2) ** 2
    )
    return 2 * RADIO_TIERRA_KM * math.asin(math.sqrt(a))


def _clave_ventana(pedido):
    """Ordena por hora limite de entrega y luego por prioridad.

    Los pedidos sin ventana horaria se ubican al final, ya que son los unicos
    que admiten holgura en la programacion.
    """
    if pedido.ventana_fin:
        return (0, pedido.ventana_fin, pedido.prioridad or 3)
    return (1, None, pedido.prioridad or 3)


def _ordenar_por_ventana(pedidos):
    con_ventana = sorted([p for p in pedidos if p.ventana_fin], key=_clave_ventana)
    sin_ventana = sorted([p for p in pedidos if not p.ventana_fin], key=lambda p: p.prioridad or 3)
    return con_ventana + sin_ventana


def _vecino_mas_cercano(origen, pedidos):
    """Heuristica local de ruteo usada cuando el proveedor externo no responde."""
    pendientes = list(pedidos)
    ordenados = []
    lat_actual, lng_actual = origen
    distancia_total = 0.0

    while pendientes:
        siguiente = min(
            pendientes,
            key=lambda p: distancia_haversine(lat_actual, lng_actual, p.latitud, p.longitud),
        )
        distancia_total += distancia_haversine(
            lat_actual, lng_actual, siguiente.latitud, siguiente.longitud
        )
        lat_actual, lng_actual = siguiente.latitud, siguiente.longitud
        ordenados.append(siguiente)
        pendientes.remove(siguiente)

    return ordenados, distancia_total


def _distancia_recorrido(origen, pedidos):
    """Distancia acumulada de un recorrido ya ordenado."""
    total = 0.0
    lat_actual, lng_actual = origen
    for pedido in pedidos:
        total += distancia_haversine(lat_actual, lng_actual, pedido.latitud, pedido.longitud)
        lat_actual, lng_actual = pedido.latitud, pedido.longitud
    return total


def _coordenadas_osrm(origen, pedidos):
    """OSRM espera las coordenadas en formato longitud,latitud."""
    puntos = [f"{origen[1]},{origen[0]}"]
    puntos += [f"{p.longitud},{p.latitud}" for p in pedidos]
    return ";".join(puntos)


def _geometria_a_json(geojson):
    """Convierte la geometria GeoJSON (lng,lat) al formato [lat, lng] de Leaflet."""
    if not geojson:
        return None
    coordenadas = geojson.get("coordinates") or []
    return json.dumps([[punto[1], punto[0]] for punto in coordenadas])


def _base_osrm():
    """URL del proveedor; configurable para apuntar a una instancia propia."""
    try:
        from flask import current_app

        configurada = current_app.config.get("URL_OSRM")
        if configurada:
            return [configurada]
    except RuntimeError:
        pass  # Fuera del contexto de aplicacion
    return [URL_OSRM_SEGURA, URL_OSRM_PLANA]


def _consultar_osrm(servicio, coordenadas, parametros):
    ultimo_error = None

    for base in _base_osrm():
        url = f"{base}/{servicio}/v1/driving/{coordenadas}"
        try:
            respuesta = requests.get(url, params=parametros, timeout=TIEMPO_ESPERA)
        except requests.exceptions.SSLError as error:
            # El interprete no puede negociar TLS con este servidor: se reintenta
            # con la siguiente URL de la lista.
            ultimo_error = error
            continue

        respuesta.raise_for_status()
        datos = respuesta.json()
        if datos.get("code") != "Ok":
            raise ValueError(f"OSRM respondio: {datos.get('code')} - {datos.get('message', '')}")
        return datos

    raise ultimo_error if ultimo_error else requests.RequestException("Sin proveedor disponible")


def _ruta_osrm_optimizada(origen, pedidos):
    """Servicio /trip: OSRM decide el orden de las paradas (TSP abierto)."""
    datos = _consultar_osrm(
        "trip",
        _coordenadas_osrm(origen, pedidos),
        {
            "source": "first",        # Siempre parte del centro de distribucion
            "roundtrip": "false",     # No exige regresar al origen
            "destination": "any",
            "overview": "full",
            "geometries": "geojson",
        },
    )

    viaje = datos["trips"][0]

    # waypoints[i]["waypoint_index"] indica la posicion de la entrada i en el
    # recorrido optimizado. La entrada 0 es el centro de distribucion.
    posiciones = []
    for indice_entrada, waypoint in enumerate(datos["waypoints"][1:], start=0):
        posiciones.append((waypoint["waypoint_index"], pedidos[indice_entrada]))

    ordenados = [pedido for _, pedido in sorted(posiciones, key=lambda par: par[0])]

    return ResultadoRuteo(
        orden=[p.id for p in ordenados],
        distancia_km=round(viaje["distance"] / 1000, 2),
        duracion_min=round(viaje["duration"] / 60, 1),
        geometria=_geometria_a_json(viaje.get("geometry")),
        proveedor="OSRM",
    )


def _ruta_osrm_orden_fijo(origen, pedidos):
    """Servicio /route: respeta el orden dado y devuelve la geometria real."""
    datos = _consultar_osrm(
        "route",
        _coordenadas_osrm(origen, pedidos),
        {"overview": "full", "geometries": "geojson"},
    )
    ruta = datos["routes"][0]

    return ResultadoRuteo(
        orden=[p.id for p in pedidos],
        distancia_km=round(ruta["distance"] / 1000, 2),
        duracion_min=round(ruta["duration"] / 60, 1),
        geometria=_geometria_a_json(ruta.get("geometry")),
        proveedor="OSRM",
    )


def calcular_ruta(origen, pedidos, estrategia=ESTRATEGIA_DISTANCIA):
    """Calcula la secuencia de entrega de un conjunto de pedidos.

    `origen` es la tupla (latitud, longitud) del centro de distribucion.
    Los pedidos sin coordenadas no pueden enrutarse: se ubican al final de la
    secuencia y se reportan como advertencia para que el despachador los
    geolocalice manualmente.
    """
    advertencias = []

    geolocalizados = [p for p in pedidos if p.latitud is not None and p.longitud is not None]
    sin_coordenadas = [p for p in pedidos if p.latitud is None or p.longitud is None]

    if sin_coordenadas:
        advertencias.append(
            f"{len(sin_coordenadas)} pedido(s) sin coordenadas quedaron al final de la "
            "secuencia y no se incluyen en el calculo de distancia."
        )

    if not geolocalizados:
        return ResultadoRuteo(
            orden=[p.id for p in sin_coordenadas],
            distancia_km=0.0,
            duracion_min=0.0,
            geometria=None,
            proveedor="LOCAL",
            advertencias=advertencias
            + ["Ningun pedido tiene coordenadas: no fue posible trazar la ruta."],
        )

    if estrategia == ESTRATEGIA_VENTANA:
        secuencia = _ordenar_por_ventana(geolocalizados)
        try:
            resultado = _ruta_osrm_orden_fijo(origen, secuencia)
        except (requests.RequestException, ValueError, KeyError, IndexError) as error:
            resultado = ResultadoRuteo(
                orden=[p.id for p in secuencia],
                distancia_km=round(_distancia_recorrido(origen, secuencia), 2),
                duracion_min=0.0,
                geometria=None,
                proveedor="LOCAL",
            )
            advertencias.append(
                "No se pudo contactar el servicio de geolocalizacion "
                f"({type(error).__name__}). Se ordeno por ventana horaria y la distancia "
                "es una estimacion en linea recta."
            )
    else:
        try:
            resultado = _ruta_osrm_optimizada(origen, geolocalizados)
        except (requests.RequestException, ValueError, KeyError, IndexError) as error:
            secuencia, distancia = _vecino_mas_cercano(origen, geolocalizados)
            resultado = ResultadoRuteo(
                orden=[p.id for p in secuencia],
                distancia_km=round(distancia, 2),
                duracion_min=0.0,
                geometria=None,
                proveedor="LOCAL",
            )
            advertencias.append(
                "No se pudo contactar el servicio de geolocalizacion "
                f"({type(error).__name__}). Se aplico la heuristica local de vecino mas "
                "cercano y la distancia es una estimacion en linea recta."
            )

    resultado.orden = resultado.orden + [p.id for p in sin_coordenadas]
    resultado.advertencias = advertencias + resultado.advertencias
    return resultado
