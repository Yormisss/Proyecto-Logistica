"""Configuracion del negocio: catalogo de motivos de fallo y centro de distribucion.

Corre sin internet: el ruteo se simula sin red y Retell se simula con firmas
generadas por su propio SDK.
"""

import html as html_lib
import json
import pathlib
import re
import sys
from datetime import timedelta
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import requests
from retell.lib.webhook_auth import symmetric
from sqlalchemy import inspect, text

from run import app
from app.extensions import db
from app.models import (EstadoPedido, EstadoRuta, MotivoFallo, Pedido, PruebaEntrega, Rol, Ruta,
                        SesionAsistente, Usuario)
from app.services import analitica, ruteo
from app.services import configuracion as conf
from app.services.planificacion import origen_centro_distribucion
from app.tiempo import ahora, hoy

fallos = []
def check(c, m):
    print(("  OK   " if c else "  FALLA") + f" {m}")
    if not c: fallos.append(m)

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _preparar import reiniciar_base
reiniciar_base()

CLAVE = "clave-retell-prueba"
app.config["WTF_CSRF_ENABLED"] = True
app.config.update(RETELL_API_KEY=CLAVE, MAKE_WEBHOOK_URL="", CORREO_OPERACIONES="",
                  CD_NOMBRE="Centro del env", CD_LAT=4.6, CD_LNG=-74.1)

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

def mensajes(r):
    return html_lib.unescape(" ".join(re.findall(r'class="alerta alerta-\w+">([^<]+)<', r.data.decode())))

def motivo(nombre):
    with app.app_context():
        m = db.session.query(MotivoFallo).filter_by(nombre=nombre).one_or_none()
        return SimpleNamespace(id=m.id, activo=m.activo) if m else None

admin = sesion("admin@sgds.com", "Admin123*")
gestor = sesion("despachador@sgds.com", "Despacho123*")
conductor = sesion("conductor1@sgds.com", "Conductor123*")
cliente = sesion("cliente@sgds.com", "Cliente123*")
URL = "/admin/configuracion/"


print("\n== 1. Carga inicial ==")
with app.app_context():
    nombres = [m.nombre for m in conf.catalogo()]
    centro = conf.centro_distribucion()
check(nombres == list(conf.MOTIVOS_INICIALES) + ["Otro"],
      f"el catalogo nace con los motivos que estaban fijos en el codigo y 'Otro' al final ({len(nombres)})")
check(centro["nombre"] == "Centro de Distribucion Bogota",
      "el centro de distribucion se tomo del .env la primera vez y ahora manda la tabla")
with app.app_context():
    sin_id = db.session.query(PruebaEntrega).filter(PruebaEntrega.motivo_fallo.isnot(None),
                                                    PruebaEntrega.motivo_id.is_(None)).count()
check(sin_id == 0, "los fallos sembrados quedan vinculados a su motivo del catalogo")


print("\n== 2. Acceso a la pantalla ==")
check(admin.get(URL).status_code == 200, "el admin entra a Configuracion")
check('href="/admin/configuracion/"' in admin.get("/admin/").data.decode(), "con su enlace en el menu")
for nombre, c in (("gestor", gestor), ("conductor", conductor), ("cliente", cliente)):
    r = c.get(URL)
    check(r.status_code in (302, 403), f"{nombre} no puede entrar ({r.status_code})")
    r = c.post(f"{URL}motivos", data={"csrf_token": token(c, "/auth/perfil"), "nombre": "Intruso"})
    check(r.status_code in (302, 403) and motivo("Intruso") is None, f"{nombre} no puede agregar motivos")


print("\n== 3. Catalogo de motivos ==")
def agregar(nombre):
    return admin.post(f"{URL}motivos", data={"csrf_token": token(admin, URL), "nombre": nombre},
                      follow_redirects=True)
r = agregar("Lluvia fuerte")
check("Motivo 'Lluvia fuerte' agregado." in mensajes(r) and motivo("Lluvia fuerte").activo, "agregar un motivo")
for nombre, esperado, que in (
        ("", "Indique el nombre del motivo.", "vacio"),
        ("  LLUVIA   fuerte. ", "Ya existe el motivo 'Lluvia fuerte'.", "repetido con otra escritura"),
        ("otro", "'Otro' ya existe y es el motivo comodín.", "llamado como el comodin"),
        ("x" * 161, "no puede pasar de 160 caracteres", "de mas de 160 caracteres")):
    r = agregar(nombre)
    check(esperado in mensajes(r), f"rechaza un motivo {que}")

def renombrar(motivo_id, nombre):
    return admin.post(f"{URL}motivos/{motivo_id}/renombrar",
                      data={"csrf_token": token(admin, URL), "nombre": nombre}, follow_redirects=True)
def alternar(motivo_id):
    return admin.post(f"{URL}motivos/{motivo_id}/estado", data={"csrf_token": token(admin, URL)},
                      follow_redirects=True)

lluvia = motivo("Lluvia fuerte").id
r = renombrar(lluvia, "Lluvia intensa")
check("renombrado a 'Lluvia intensa'" in mensajes(r) and motivo("Lluvia intensa").id == lluvia, "renombrar")
r = renombrar(lluvia, "Cliente ausente")
check("Ya existe el motivo 'Cliente ausente'." in mensajes(r), "no se renombra a un nombre que ya existe")
otro = motivo("Otro").id
r = renombrar(otro, "Varios")
check("no se puede renombrar" in mensajes(r) and motivo("Otro") is not None, "'Otro' no se puede renombrar")
r = alternar(otro)
check("no se puede activar ni desactivar" in mensajes(r) and motivo("Otro").activo, "'Otro' no se puede desactivar")
r = alternar(lluvia)
check("desactivado" in mensajes(r) and not motivo("Lluvia intensa").activo, "desactivar un motivo")
r = agregar("Lluvia intensa")
check("(está desactivado; actívelo)" in mensajes(r), "agregar uno desactivado indica que hay que activarlo")
check(admin.post(f"{URL}motivos/{lluvia}/estado", data={}).status_code == 400 or not motivo("Lluvia intensa").activo,
      "sin token CSRF no cambia nada")
with app.app_context():
    check(db.session.query(MotivoFallo).count() == 9, "los motivos no se borran: el catalogo sigue con 9")


print("\n== 4. Pantalla del conductor ==")
with app.app_context():
    c1 = db.session.query(Usuario).filter_by(correo="conductor1@sgds.com").one()
    c1_id = c1.id
    ruta = db.session.query(Ruta).filter_by(conductor_id=c1.id, fecha=hoy()).one()
    paradas = []
    for n in range(1, 6):
        p = Pedido(codigo=f"CONF-00{n}", cliente_nombre="Tienda Prueba", direccion=f"Calle {n}",
                   fecha_despacho=hoy(), estado=EstadoPedido.EN_RUTA, ruta_id=ruta.id,
                   orden_en_ruta=90 + n, creado_por_id=c1.id)
        db.session.add(p); db.session.flush(); paradas.append(p.id)
    db.session.commit()

html = conductor.get(f"/conductor/parada/{paradas[0]}").data.decode()
opciones = re.findall(r'<option value="([^"]+)"', html)
check("Lluvia intensa" not in opciones and opciones[-1] == "Otro" and "Cliente ausente" in opciones,
      f"ofrece los motivos activos, sin los desactivados, y 'Otro' al final ({len(opciones)})")
check('name="motivo_otro"' in html, "con el campo para escribir el motivo de 'Otro'")

def fallar(pedido_id, motivo_elegido, texto_otro=""):
    url = f"/conductor/parada/{pedido_id}"
    return conductor.post(f"{url}/fallar", data={"csrf_token": token(conductor, url),
                                                 "motivo_fallo": motivo_elegido, "motivo_otro": texto_otro},
                          follow_redirects=True)
def prueba_de(pedido_id):
    with app.app_context():
        p = db.session.get(Pedido, pedido_id)
        return SimpleNamespace(estado=p.estado, texto=p.prueba_entrega.motivo_fallo if p.prueba_entrega else None,
                               motivo_id=p.prueba_entrega.motivo_id if p.prueba_entrega else None)

r = fallar(paradas[0], "Cliente ausente")
pe = prueba_de(paradas[0])
check(pe.estado == EstadoPedido.FALLIDO and pe.texto == "Cliente ausente" and pe.motivo_id == motivo("Cliente ausente").id,
      "un motivo del catalogo guarda su nombre y su motivo_id")
r = fallar(paradas[1], "Otro", "")
check("Escriba el motivo cuando elige 'Otro'." in mensajes(r) and prueba_de(paradas[1]).estado == EstadoPedido.EN_RUTA,
      "'Otro' sin texto se rechaza y no cambia el pedido")
r = fallar(paradas[1], "Otro", "y" * 161)
check("no puede pasar de 160 caracteres" in mensajes(r) and prueba_de(paradas[1]).estado == EstadoPedido.EN_RUTA,
      "'Otro' con mas de 160 caracteres se rechaza")
r = fallar(paradas[1], "Otro", "Porton con candado")
pe = prueba_de(paradas[1])
check(pe.estado == EstadoPedido.FALLIDO and pe.texto == "Porton con candado" and pe.motivo_id == otro,
      "'Otro' con texto guarda lo escrito y el motivo 'Otro'")
r = fallar(paradas[2], "Lluvia intensa")
check("Seleccione un motivo de la lista." in mensajes(r) and prueba_de(paradas[2]).estado == EstadoPedido.EN_RUTA,
      "un motivo desactivado (POST armado a mano) se rechaza")
r = fallar(paradas[2], "")
check("Indique el motivo por el cual no se pudo entregar." in mensajes(r), "sin motivo se rechaza")


print("\n== 5. Por voz: catalogo u 'Otro' ==")
with app.app_context():
    db.session.add(SesionAsistente(call_id="call_conf", usuario_id=c1_id, rol=Rol.CONDUCTOR,
                                   vence_en=ahora() + timedelta(minutes=10)))
    for n, pid in enumerate(paradas[2:], start=3):
        db.session.get(Pedido, pid).orden_en_ruta = n + 90
    db.session.commit()
retell = app.test_client()
def voz(nombre, args):
    cuerpo = json.dumps({"call": {"call_id": "call_conf"}, "name": nombre, "args": args}, ensure_ascii=False)
    r = retell.post(f"/api/asistente/conductor/{nombre}", data=cuerpo.encode(),
                    headers={"Content-Type": "application/json",
                             "X-Retell-Signature": symmetric["sign"](cuerpo, CLAVE)})
    return (r.get_json(silent=True) or {}).get("mensaje", "")

s = voz("registrar-fallo", {"orden": 93, "motivo": "cliente ausnte"})
check(s.endswith("como no entregada por: Cliente ausente. ¿confirmas?"),
      f"un motivo mal transcrito se reconoce en el catalogo y se lee asi: {s}")
voz("registrar-fallo", {"orden": 93, "motivo": "cliente ausnte", "confirmar": True})
pe = prueba_de(paradas[2])
check(pe.texto == "Cliente ausente" and pe.motivo_id == motivo("Cliente ausente").id,
      "y se registra con el nombre del catalogo")
s = voz("registrar-fallo", {"orden": 94, "motivo": "el perro no dejaba pasar"})
check(s.endswith("como no entregada por otro motivo: el perro no dejaba pasar. ¿confirmas?"),
      f"lo que no coincide se lee como 'Otro' con el texto dicho: {s}")
voz("registrar-fallo", {"orden": 94, "motivo": "el perro no dejaba pasar", "confirmar": True})
pe = prueba_de(paradas[3])
check(pe.texto == "el perro no dejaba pasar" and pe.motivo_id == otro, "y se registra como 'Otro' con ese texto")
s = voz("registrar-fallo", {"orden": 95, "motivo": "otro"})
check("Dime cuál fue el motivo" in s and not s.endswith("¿confirmas?"), "decir solo 'otro' pide el motivo")
s = voz("registrar-fallo", {"orden": 95, "motivo": "z" * 161})
check("no puede pasar de 160 caracteres" in s, "un motivo de mas de 160 caracteres se rechaza")
check(prueba_de(paradas[4]).estado == EstadoPedido.EN_RUTA, "y ninguno de esos dos cambia el pedido")


print("\n== 6. Analitica de causas de fallo ==")
with app.app_context():
    # Prueba anterior al catalogo: solo texto, y con el nombre original de un motivo renombrado.
    viejo = Pedido(codigo="CONF-VIEJO", cliente_nombre="Tienda Prueba", direccion="Calle 9",
                   fecha_despacho=hoy(), estado=EstadoPedido.FALLIDO, creado_por_id=c1_id)
    raro = Pedido(codigo="CONF-RARO", cliente_nombre="Tienda Prueba", direccion="Calle 10",
                  fecha_despacho=hoy(), estado=EstadoPedido.FALLIDO, creado_por_id=c1_id)
    db.session.add_all([viejo, raro]); db.session.flush()
    db.session.add(PruebaEntrega(pedido_id=viejo.id, motivo_fallo="Zona de dificil acceso", registrado_en=ahora()))
    db.session.add(PruebaEntrega(pedido_id=raro.id, motivo_fallo="Inundacion en la via", registrado_en=ahora()))
    db.session.commit()
zona = motivo("Zona de dificil acceso").id
renombrar(zona, "Acceso restringido")
with app.app_context():
    filas = {f["motivo"]: f for f in analitica.motivos_fallo(1)}
    check(db.session.query(Pedido).filter_by(codigo="CONF-VIEJO").one().prueba_entrega.motivo_fallo
          == "Zona de dificil acceso", "renombrar no cambia el texto de las pruebas ya registradas")
check("Acceso restringido" in filas and "Zona de dificil acceso" not in filas,
      "una prueba anterior al catalogo se agrupa en su motivo, con el nombre actual")
check(filas.get("Cliente ausente", {}).get("cantidad") == 2, "agrupa por motivo del catalogo")
detalle = {d["texto"]: d["cantidad"] for d in filas.get("Otro", {}).get("detalles", [])}
check(filas.get("Otro", {}).get("es_otro") and detalle == {"Porton con candado": 1, "el perro no dejaba pasar": 1,
                                                          "Inundacion en la via": 1},
      f"'Otro' trae el detalle de sus textos, incluida una prueba vieja sin motivo reconocible {detalle}")
html = gestor.get("/admin/analitica?dias=7").data.decode()
check("Detalle de «Otro»" in html and "Porton con candado" in html, "la pantalla de analitica muestra ese detalle")


print("\n== 7. Centro de distribucion ==")
def centro(nombre, lat, lng):
    return admin.post(f"{URL}centro", data={"csrf_token": token(admin, URL), "cd_nombre": nombre,
                                           "cd_latitud": lat, "cd_longitud": lng}, follow_redirects=True)
for datos, esperado, que in (
        (("", "4.6", "-74.1"), "Indique el nombre del centro de distribución.", "sin nombre"),
        (("CD", "95", "-74.1"), "La latitud debe ser un número entre -90 y 90.", "con latitud fuera de rango"),
        (("CD", "4.6", "-181"), "La longitud debe ser un número entre -180 y 180.", "con longitud fuera de rango"),
        (("CD", "norte", "-74.1"), "La latitud debe ser un número", "con latitud no numerica")):
    r = centro(*datos)
    check(esperado in mensajes(r), f"rechaza un centro {que}")
with app.app_context():
    check(conf.centro_distribucion()["nombre"] == "Centro de Distribucion Bogota", "y nada de eso cambia el centro")
r = centro("CD Funza", "4,7166", "-74.2116")
with app.app_context():
    nuevo = conf.centro_distribucion()
    origen = origen_centro_distribucion()
check("Centro de distribucion actualizado: CD Funza" in mensajes(r)
      and nuevo == {"nombre": "CD Funza", "latitud": 4.7166, "longitud": -74.2116},
      "editar el centro acepta la coma decimal y valida los rangos")
check(origen == (4.7166, -74.2116), "las rutas nuevas o recalculadas salen del centro de la tabla, no del .env")
check("CD Funza" in gestor.get("/admin/").data.decode(), "el tablero muestra el nombre nuevo")
with app.app_context():
    ruta_hoy = db.session.query(Ruta).filter_by(conductor_id=c1_id, fecha=hoy()).one()
    check(ruta_hoy.distancia_km is not None, "las rutas ya trazadas no se tocan al cambiar el centro")


print("\n== 8. Migracion m005 ==")
from migraciones.m005_configuracion_negocio import aplicar
with app.app_context():
    hecho = aplicar(verboso=False)
    check(not any(hecho.values()), f"sobre una base al dia no hace nada ({hecho})")
    if db.engine.dialect.name == "sqlite":
        # Base anterior a la configuracion: sin las tablas ni la columna.
        # SQLite no quita una columna con clave ajena: se reconstruye la tabla
        # sin ella, con los mismos datos.
        columnas = [c["name"] for c in inspect(db.engine).get_columns("pruebas_entrega")
                    if c["name"] != "motivo_id"]
        lista = ", ".join(columnas)
        with db.engine.begin() as conexion:
            conexion.execute(text("PRAGMA foreign_keys=OFF"))
            conexion.execute(text(f"CREATE TABLE pruebas_entrega_vieja AS SELECT {lista} FROM pruebas_entrega"))
            conexion.execute(text("DROP TABLE pruebas_entrega"))
            conexion.execute(text("ALTER TABLE pruebas_entrega_vieja RENAME TO pruebas_entrega"))
            conexion.execute(text("DROP TABLE motivos_fallo"))
            conexion.execute(text("DROP TABLE configuracion_negocio"))
        db.session.remove()
        hecho = aplicar(verboso=False)
        check(set(hecho["tablas"]) == {"motivos_fallo", "configuracion_negocio"}
              and hecho["columnas"] == ["pruebas_entrega.motivo_id"] and len(hecho["motivos"]) == 8
              and hecho["indices"] == ["ix_pruebas_entrega_motivo_id"]
              and hecho["centro"], f"sobre una base anterior crea todo y carga el catalogo ({hecho})")
        columnas = {c["name"] for c in inspect(db.engine).get_columns("pruebas_entrega")}
        check("motivo_id" in columnas, "pruebas_entrega gana motivo_id")
        check(db.session.query(PruebaEntrega).filter(PruebaEntrega.motivo_id.isnot(None)).count() == 0,
              "sin tocar las pruebas ya registradas (el historico no se edita)")
        check(conf.centro_distribucion()["nombre"] == "Centro del env",
              "el centro de distribucion se toma del .env la primera vez")
        check(not any(aplicar(verboso=False).values()), "volver a ejecutarla no cambia nada")
    else:
        check(db.engine.dialect.name == "mysql" and "motivo_id" in {
            fk["constrained_columns"][0] for fk in inspect(db.engine).get_foreign_keys("pruebas_entrega")},
              "en MySQL, pruebas_entrega.motivo_id declara la clave ajena")
        # Base creada cuando las coordenadas eran FLOAT (precision simple).
        with db.engine.begin() as conexion:
            conexion.execute(text("ALTER TABLE configuracion_negocio MODIFY cd_latitud FLOAT NOT NULL"))
            conexion.execute(text("ALTER TABLE configuracion_negocio MODIFY cd_longitud FLOAT NOT NULL"))
        db.session.remove()
        hecho = aplicar(verboso=False)
        check(hecho["precision"] == ["cd_latitud", "cd_longitud"],
              f"en MySQL pasa las coordenadas del centro de FLOAT a DOUBLE ({hecho['precision']})")
        tipos = {c["name"]: type(c["type"]).__name__.upper()
                 for c in inspect(db.engine).get_columns("configuracion_negocio")}
        check(tipos["cd_latitud"] == tipos["cd_longitud"] == "DOUBLE", f"quedan DOUBLE ({tipos['cd_latitud']})")
        conf.editar_centro("CD Preciso", "4.6482837", "-74.2478938", None)
        db.session.commit()
        db.session.remove()
        centro = conf.centro_distribucion()
        check((centro["latitud"], centro["longitud"]) == (4.6482837, -74.2478938),
              f"y conservan las coordenadas completas ({centro['latitud']}, {centro['longitud']})")
        check(not any(aplicar(verboso=False).values()), "volver a ejecutarla no cambia nada")


print("\n" + "=" * 55)
print("RESULTADO: " + ("TODAS LAS PRUEBAS PASARON" if not fallos else f"{len(fallos)} FALLAS"))
for f in fallos: print("   - " + f)
sys.exit(1 if fallos else 0)
