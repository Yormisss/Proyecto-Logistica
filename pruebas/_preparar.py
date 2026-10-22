"""Modulo comun de las pruebas: se importa primero en cada suite.

Al importarse, antes de que la suite cargue la aplicacion:

- Apunta DATABASE_URL a la base de pruebas (PRUEBAS_DATABASE_URL o
  BASE_PRUEBAS_POR_DEFECTO) y se niega a seguir si su nombre no termina en
  "_pruebas": las suites borran la base y no deben tocar la del .env.
- Vacia MAKE_WEBHOOK_URL, MAKE_WEBHOOK_CREDENCIALES_URL, RETELL_API_KEY y
  SEMILLA_CORREO_CLIENTE. load_dotenv no pisa una variable ya definida, asi que
  vacias ganan sobre el .env del desarrollador.
- Bloquea la red: solo se permiten conexiones a la propia maquina (MySQL). Los
  servicios externos (OSRM, Make, Retell) fallan al instante y la aplicacion
  usa su respaldo. Solo la parte de OSRM en vivo de la suite 03 abre la red,
  con `red_externa()`, y solo si se pidio --externas.

`reiniciar_base()` deja la base vacia y sembrada; cada suite la llama una sola
vez al arrancar, de modo que tambien funciona corrida sola.
"""

import contextlib
import io
import json
import math
import os
import pathlib
import socket
import sys
from urllib.parse import urlsplit

RAIZ = pathlib.Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

# El interprete que ya esta ejecutando la suite es el mismo que tiene las
# dependencias del proyecto instaladas, sea Windows, macOS o Linux.
PYTHON = sys.executable

BASE_PRUEBAS_POR_DEFECTO = "mysql+pymysql://sgds:sgds_clave@127.0.0.1:3307/logistica_pruebas"
SUFIJO_OBLIGATORIO = "_pruebas"

# Pide la parte de OSRM en vivo de la suite 03 (ejecutar_todas.py --externas).
EXTERNAS = "--externas" in sys.argv or os.environ.get("PRUEBAS_EXTERNAS") == "1"


def url_base_pruebas():
    return os.environ.get("PRUEBAS_DATABASE_URL") or BASE_PRUEBAS_POR_DEFECTO


def nombre_base(url):
    return urlsplit(url).path.lstrip("/").split("?")[0]


def validar_base(url):
    """Termina el proceso si la base no es de pruebas."""
    nombre = nombre_base(url)
    if not nombre.endswith(SUFIJO_OBLIGATORIO):
        sys.exit(f"Las pruebas borran la base y solo corren en una que termine en "
                 f"'{SUFIJO_OBLIGATORIO}'. Base indicada: '{nombre or url}'.")


BASE_PRUEBAS = url_base_pruebas()
validar_base(BASE_PRUEBAS)

VARIABLES_PRUEBAS = {
    "DATABASE_URL": BASE_PRUEBAS,
    "MAKE_WEBHOOK_URL": "",
    "MAKE_WEBHOOK_CREDENCIALES_URL": "",
    "RETELL_API_KEY": "",
    "SEMILLA_CORREO_CLIENTE": "",
}
os.environ.update(VARIABLES_PRUEBAS)
# Entorno para los subprocesos que lancen las suites (flask, seed.py).
ENTORNO_PRUEBAS = dict(os.environ)


# ---- Red: solo la propia maquina ----
_HOSTS_LOCALES = {"localhost", "127.0.0.1", "::1", urlsplit(BASE_PRUEBAS).hostname or "127.0.0.1"}
_getaddrinfo_original = socket.getaddrinfo
_connect_original = socket.socket.connect
_red_abierta = False


def _es_local(host):
    if isinstance(host, bytes):
        host = host.decode()
    return host is None or host in _HOSTS_LOCALES or str(host).startswith("127.")


def _getaddrinfo(host, *args, **kwargs):
    if not _red_abierta and not _es_local(host):
        raise socket.gaierror(f"Pruebas sin red externa: no se resuelve {host}")
    return _getaddrinfo_original(host, *args, **kwargs)


def _connect(self, direccion):
    if not _red_abierta and isinstance(direccion, tuple) and not _es_local(direccion[0]):
        raise ConnectionRefusedError(f"Pruebas sin red externa: no se conecta a {direccion[0]}")
    return _connect_original(self, direccion)


socket.getaddrinfo = _getaddrinfo
socket.socket.connect = _connect


@contextlib.contextmanager
def red_externa():
    """Abre la red solo dentro del bloque (OSRM en vivo de la suite 03)."""
    global _red_abierta
    anterior, _red_abierta = _red_abierta, True
    try:
        yield
    finally:
        _red_abierta = anterior


# ---- OSRM simulado ----
class _RespuestaOsrm:
    status_code = 200

    def __init__(self, datos):
        self._datos = datos

    def raise_for_status(self):
        pass

    def json(self):
        return self._datos


def _km(a, b):
    lng1, lat1, lng2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def _tramo(puntos):
    """Distancia (m), duracion (s) y geometria GeoJSON de un recorrido."""
    metros = sum(_km(a, b) for a, b in zip(puntos, puntos[1:])) * 1300   # 30 % sobre la recta
    linea = []
    for a, b in zip(puntos, puntos[1:]):
        linea += [[a[0] + (b[0] - a[0]) * i / 8, a[1] + (b[1] - a[1]) * i / 8] for i in range(8)]
    linea.append(list(puntos[-1]))
    return metros, metros / 1000 / 25 * 3600, {"type": "LineString", "coordinates": linea}


def osrm_simulado(url, params=None, timeout=None):
    """Reemplazo de requests.get para OSRM: /trip por vecino mas cercano y /route
    en el orden dado, con distancias en linea recta mas un 30 %."""
    texto = url.rsplit("/", 1)[1]
    puntos = [tuple(map(float, par.split(","))) for par in texto.split(";")]
    if "/trip/" in url:
        pendientes, orden = list(range(1, len(puntos))), [0]
        while pendientes:
            siguiente = min(pendientes, key=lambda i: _km(puntos[orden[-1]], puntos[i]))
            pendientes.remove(siguiente)
            orden.append(siguiente)
        metros, segundos, geometria = _tramo([puntos[i] for i in orden])
        posicion = {indice: pos for pos, indice in enumerate(orden)}
        return _RespuestaOsrm({
            "code": "Ok",
            "trips": [{"distance": metros, "duration": segundos, "geometry": geometria}],
            "waypoints": [{"waypoint_index": posicion[i]} for i in range(len(puntos))],
        })
    metros, segundos, geometria = _tramo(puntos)
    return _RespuestaOsrm({"code": "Ok", "routes": [{"distance": metros, "duration": segundos,
                                                    "geometry": geometria}]})


# ---- Base de datos ----
# Sembrar cuesta unos 8 s (crear el esquema y ~2000 consultas). ejecutar_todas.py
# siembra una sola vez, guarda las filas en el archivo de PRUEBAS_SEMILLA y cada
# suite las restaura en menos de un segundo. Corrida sola, la suite siembra.
ARCHIVO_SEMILLA = os.environ.get("PRUEBAS_SEMILLA", "")


def _firma_esquema(conexion):
    """Columnas e indices de la base: si una suite altero el esquema (p. ej.
    al probar una migracion), la restauracion lo vuelve a crear."""
    from sqlalchemy import text

    columnas = conexion.execute(text(
        "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() ORDER BY 1, 2")).all()
    indices = conexion.execute(text(
        "SELECT TABLE_NAME, INDEX_NAME, NON_UNIQUE, SEQ_IN_INDEX, COLUMN_NAME "
        "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() ORDER BY 1, 2, 4")).all()
    return repr([tuple(f) for f in columnas] + [tuple(f) for f in indices])


def _esquema_nuevo(db):
    db.session.remove()
    db.engine.dispose()
    db.drop_all()
    db.create_all()


def _restaurar(db, semilla):
    from sqlalchemy import text

    tablas = db.metadata.sorted_tables
    db.session.remove()
    with db.engine.connect() as conexion:
        igual = _firma_esquema(conexion) == semilla["firma"]
    if not igual:
        _esquema_nuevo(db)
    with db.engine.begin() as conexion:
        conexion.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        if igual:
            for tabla in tablas:
                conexion.execute(text(f"DELETE FROM `{tabla.name}`"))
                conexion.execute(text(f"ALTER TABLE `{tabla.name}` AUTO_INCREMENT = 1"))
        for tabla in tablas:
            if semilla["filas"].get(tabla.name):
                conexion.execute(tabla.insert(), semilla["filas"][tabla.name])
        conexion.execute(text("SET FOREIGN_KEY_CHECKS = 1"))


def _semilla_guardada():
    import pickle

    from app.tiempo import hoy

    if not ARCHIVO_SEMILLA or not os.path.exists(ARCHIVO_SEMILLA):
        return None
    with open(ARCHIVO_SEMILLA, "rb") as archivo:
        semilla = pickle.load(archivo)
    return semilla if semilla["fecha"] == hoy() else None


def reiniciar_base(**variables):
    """Deja la base de pruebas vacia y sembrada con los datos de demostracion.

    Corre en el mismo proceso de la suite (sin lanzar flask ni seed.py aparte).
    `variables` se ponen en el entorno solo durante la siembra, p. ej.
    SEMILLA_CORREO_CLIENTE; con ellas siempre se siembra de nuevo.
    """
    from run import _configuracion_inicial, app
    from app.extensions import db
    from seed import sembrar_datos

    anteriores = {clave: os.environ.get(clave) for clave in variables}
    os.environ.update(variables)
    try:
        with app.app_context():
            semilla = None if variables else _semilla_guardada()
            if semilla:
                _restaurar(db, semilla)
            else:
                _esquema_nuevo(db)
                _configuracion_inicial()
                with contextlib.redirect_stdout(io.StringIO()):
                    sembrar_datos()
            db.session.remove()
    finally:
        for clave, valor in anteriores.items():
            if valor is None:
                os.environ.pop(clave, None)
            else:
                os.environ[clave] = valor


def guardar_semilla(ruta):
    """Siembra una vez y guarda filas y firma del esquema (lo usa ejecutar_todas.py)."""
    import pickle

    from sqlalchemy import select

    from run import app
    from app.extensions import db
    from app.tiempo import hoy

    reiniciar_base()
    with app.app_context(), db.engine.connect() as conexion:
        semilla = {
            "fecha": hoy(),
            "firma": _firma_esquema(conexion),
            "filas": {tabla.name: [dict(fila._mapping) for fila in conexion.execute(select(tabla))]
                      for tabla in db.metadata.sorted_tables},
        }
    with open(ruta, "wb") as archivo:
        pickle.dump(semilla, archivo)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--guardar-semilla":
        guardar_semilla(sys.argv[2])
    else:
        reiniciar_base()
        print(json.dumps({"base": nombre_base(BASE_PRUEBAS), "reiniciada": True}))
