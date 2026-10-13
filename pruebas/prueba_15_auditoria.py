"""Auditoria, bitacora y eventos con quien origino la accion.

Corre sin internet: Make se simula y las firmas X-Retell-Signature se generan
con el propio SDK de Retell para probar la verificacion real.
"""

import json
import pathlib
import re
import sys
from datetime import timedelta
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from retell.lib.webhook_auth import symmetric
from sqlalchemy import inspect, text

from run import app
from app.controllers.asistente_api import exigir_confirmacion, funcion_asistente, responder
from app.extensions import db
from app.models import (Auditoria, MotivoFallo, Producto, Rol, SesionAsistente, Usuario,
                        VIGENCIA_SESION)
from app.services import notificaciones
from app.services.auditoria import limpiar, registrar_auditoria
from app.tiempo import ahora

# La consola de Windows (cp1252) no tiene la flecha de los detalles.
sys.stdout.reconfigure(errors="backslashreplace")

fallos = []
def check(c, m):
    print(("  OK   " if c else "  FALLA") + f" {m}")
    if not c: fallos.append(m)

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _preparar import reiniciar_base
reiniciar_base()

CLAVE = "clave-retell-prueba"
OPERACIONES = "operaciones@sgds.invalid"
URL_PUBLICA = "https://sgds.ejemplo.com"
app.config["WTF_CSRF_ENABLED"] = True
app.config.update(RETELL_API_KEY=CLAVE, MAKE_WEBHOOK_URL="https://hook.make.invalid/sgds",
                  MAKE_WEBHOOK_KEY="", CORREO_OPERACIONES=OPERACIONES, URL_PUBLICA=URL_PUBLICA + "/")

envios_make = []
notificaciones.requests.post = lambda url, json=None, timeout=None, headers=None: (
    envios_make.append(json), SimpleNamespace(raise_for_status=lambda: None))[1]

def eventos(tipo, desde=0):
    notificaciones.esperar_envios()
    return [e for e in envios_make[desde:] if e.get("tipo") == tipo]

CAMPOS_ACTOR = {"realizado_por", "rol_realizado_por", "origen", "realizado_por_publico"}


# ---- Utilidades ----
def sesion(correo, clave):
    c = app.test_client()
    html = c.get("/auth/login").data.decode()
    tok = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html).group(1)
    c.post("/auth/login", data={"csrf_token": tok, "correo": correo, "contrasena": clave})
    return c

def token(c, url):
    return re.search(r'name="csrf_token"[^>]*value="([^"]+)"', c.get(url).data.decode()).group(1)

retell = app.test_client()
def funcion(ruta, call_id, args=None):
    cuerpo = json.dumps({"call": {"call_id": call_id}, "name": ruta.split("/")[-1], "args": args or {}},
                        ensure_ascii=False)
    r = retell.post(f"/api/asistente/{ruta}", data=cuerpo.encode(),
                    headers={"Content-Type": "application/json",
                             "X-Retell-Signature": symmetric["sign"](cuerpo, CLAVE)})
    return r.status_code, (r.get_json(silent=True) or {})

def abrir_llamada(correo, call_id):
    with app.app_context():
        usuario = db.session.query(Usuario).filter_by(correo=correo).one()
        db.session.add(SesionAsistente(call_id=call_id, usuario_id=usuario.id, rol=usuario.rol,
                                       vence_en=ahora() + VIGENCIA_SESION))
        db.session.commit()
        return usuario.id, usuario.nombre

def registros(**filtros):
    with app.app_context():
        return [SimpleNamespace(**{c.name: getattr(r, c.name) for c in Auditoria.__table__.columns},
                                detalle_legible=r.detalle_legible, detalle_dict=r.detalle_dict)
                for r in db.session.query(Auditoria).filter_by(**filtros).order_by(Auditoria.id)]

def total_registros():
    with app.app_context():
        return db.session.query(Auditoria).count()


# Acciones de prueba, solo en este proceso: una que falla despues de confirmar
# (la accion se revierte) y otra que revienta con una excepcion.
@funcion_asistente("gestor", "accion-fallida", roles=(Rol.DESPACHADOR,), accion=True,
                   descripcion="Solo para pruebas.", parametros={"valor": {"type": "string"}})
def accion_fallida():
    pendiente = exigir_confirmacion("Voy a intentar algo que no saldra.")
    if pendiente:
        return pendiente
    db.session.rollback()
    return responder("No pude hacerlo.")

@funcion_asistente("gestor", "accion-rota", roles=(Rol.DESPACHADOR,), accion=True,
                   descripcion="Solo para pruebas.")
def accion_rota():
    pendiente = exigir_confirmacion("Voy a romper algo.")
    if pendiente:
        return pendiente
    raise RuntimeError("falla simulada")


print("\n== 1. Migracion m006 ==")
from migraciones import m006_auditoria
with app.app_context():
    db.session.remove()
    with db.engine.begin() as conexion:
        conexion.execute(text("DROP TABLE auditoria"))
    check("auditoria" not in inspect(db.engine).get_table_names(), "base sin la tabla auditoria")
    hecho = m006_auditoria.aplicar(verboso=False)
    check(hecho["tablas"] == ["auditoria"], "la migracion crea la tabla")
    indices = {tuple(i["column_names"]) for i in inspect(db.engine).get_indexes("auditoria")}
    check({("registrado_en",), ("accion",), ("origen",), ("usuario_id",)} <= indices,
          "con indices para los filtros de la bitacora")
    check(m006_auditoria.aplicar(verboso=False) == {"tablas": []}, "es idempotente: la segunda vez no hace nada")


print("\n== 2. Servicio de auditoria ==")
limpio = limpiar({"nombre": "Ana", "contrasena": "x1", "datos": {"Password": "y", "api_key": "k",
                  "lista": [{"token": "t", "valor": 3}]}, "clave_nueva": "z"})
check(limpio == {"nombre": "Ana", "contrasena": "(no se registra)",
                 "datos": {"Password": "(no se registra)", "api_key": "(no se registra)",
                           "lista": [{"token": "(no se registra)", "valor": 3}]},
                 "clave_nueva": "(no se registra)"},
      "limpiar quita contrasenas, claves y tokens en cualquier nivel")

with app.app_context():
    admin = db.session.query(Usuario).filter_by(correo="admin@sgds.com").one()
    admin_id, admin_nombre = admin.id, admin.nombre
    inicio = len(envios_make)
    registro = registrar_auditoria("Accion de prueba", "prueba", "Resumen de prueba.",
                                   {"campo": ["antes", "despues"], "contrasena": "secreta1",
                                    "activo": True, "vacio": None}, usuario=admin, origen="pantalla")
    db.session.commit()
    registro_id = registro.id
    check(registro.detalle_legible == "campo: antes → despues\ncontrasena: (no se registra)\n"
                                      "activo: sí\nvacio: (vacío)",
          f"detalle legible en texto plano: {registro.detalle_legible!r}")
    check("secreta1" not in (registro.detalle or ""), "la contrasena no se guarda")

    registro.resumen = "cambiado"
    try:
        db.session.commit()
        check(False, "modificar un registro de auditoria falla")
    except ValueError:
        db.session.rollback()
        check(True, "modificar un registro de auditoria falla (solo se agregan)")
    db.session.delete(db.session.get(Auditoria, registro_id))
    try:
        db.session.commit()
        check(False, "borrar un registro de auditoria falla")
    except ValueError:
        db.session.rollback()
        check(True, "borrar un registro de auditoria falla")
    check(db.session.get(Auditoria, registro_id).resumen == "Resumen de prueba.", "el registro sigue intacto")

    sistema = notificaciones.datos_actor()
    check(sistema == {"realizado_por": "Sistema SGDS (automático)", "rol_realizado_por": "Sistema",
                      "origen": "automático", "realizado_por_publico": "Sistema"},
          "fuera de una peticion, el actor es el sistema, automatico")

evento = eventos("accion_asistente", inicio)
check(len(evento) == 1, "cada registro envia un evento accion_asistente")
if evento:
    e = evento[0]
    check(set(e) == {"tipo", "usuario", "rol", "accion", "entidad", "resumen", "detalle", "resultado",
                     "hora", "correo_destino"} | CAMPOS_ACTOR, f"campos del evento {sorted(e)}")
    check(e["correo_destino"] == OPERACIONES and e["usuario"] == admin_nombre
          and e["rol"] == "Administrador" and e["origen"] == "pantalla" and e["resultado"] == "exito",
          "va a CORREO_OPERACIONES con usuario, rol legible, origen y resultado")
    check("secreta1" not in json.dumps(e, ensure_ascii=False) and "→" in e["detalle"],
          "con el detalle legible y sin la contrasena")
    check(re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d", e["hora"]) is not None, "hora AAAA-MM-DD HH:MM")


print("\n== 3. Acciones por voz ==")
gestor_id, gestor_nombre = abrir_llamada("despachador@sgds.com", "call_auditoria_gestor")
abrir_llamada("admin@sgds.com", "call_auditoria_admin")

antes = total_registros()
funcion("gestor/stock-producto", "call_auditoria_gestor", {"producto": "SKU-1003"})
check(total_registros() == antes, "una consulta no deja registro")

argumentos = {"producto": "SKU-1003", "valor": 55, "motivo": "Conteo fisico"}
s = funcion("admin/ajustar-inventario", "call_auditoria_admin", argumentos)
resumen_leido = s[1].get("mensaje", "")
check(resumen_leido.endswith("¿confirmas?") and total_registros() == antes,
      "el resumen sin confirmar no deja registro")
inicio = len(envios_make)
s = funcion("admin/ajustar-inventario", "call_auditoria_admin", {**argumentos, "confirmar": True})
check("quedó en 55" in s[1].get("mensaje", ""), "la accion confirmada se ejecuta")
nuevos = registros()[-1:]
if nuevos:
    r = nuevos[0]
    check(r.origen == "voz" and r.usuario_id == admin_id and r.rol == Rol.ADMIN
          and r.call_id == "call_auditoria_admin" and r.resultado == "exito",
          "queda en la auditoria: usuario, rol, origen voz, call_id y exito")
    check(r.accion == "Ajustar inventario" and r.entidad == "producto",
          f"con la accion y la entidad ({r.accion}, {r.entidad})")
    check(r.resumen == resumen_leido.replace(" ¿confirmas?", ""), "el resumen es el que se leyo al usuario")
    check(r.detalle_dict == {"funcion": "admin/ajustar-inventario", "datos": argumentos,
                             "respuesta": "Listo, el stock de SKU-1003 quedó en 55."},
          f"el detalle lleva los datos confirmados y la respuesta {r.detalle_dict}")
e = eventos("accion_asistente", inicio)
check(len(e) == 1 and e[0]["origen"] == "voz" and e[0]["realizado_por"] == admin_nombre
      and e[0]["accion"] == "Ajustar inventario", "y envia accion_asistente con origen voz")

antes = total_registros()
funcion("gestor/accion-fallida", "call_auditoria_gestor", {"valor": "x"})
s = funcion("gestor/accion-fallida", "call_auditoria_gestor", {"valor": "x", "confirmar": True})
r = registros()[-1]
check(total_registros() == antes + 1 and r.resultado == "error" and r.usuario_id == gestor_id
      and r.detalle_dict.get("respuesta") == "No pude hacerlo.",
      "una accion confirmada que no se completa queda con resultado error")

funcion("gestor/accion-rota", "call_auditoria_gestor")
try:
    estado = funcion("gestor/accion-rota", "call_auditoria_gestor", {"confirmar": True})[0]
except RuntimeError:
    estado = 500          # con DEBUG la excepcion llega hasta el cliente de pruebas
r = registros()[-1]
check(estado == 500 and r.resultado == "error" and r.accion == "Accion rota",
      f"una excepcion despues de confirmar responde 500 y queda como error ({estado})")

funcion("gestor/accion-fallida", "call_auditoria_gestor", {"valor": "y"})
antes = total_registros()
funcion("gestor/accion-fallida", "call_auditoria_gestor", {"valor": "otro", "confirmar": True})
check(total_registros() == antes, "confirmar algo distinto de lo resumido no ejecuta ni registra")


print("\n== 4. Usuarios en pantalla ==")
admin_web = sesion("admin@sgds.com", "Admin123*")
CLAVE_NUEVA = "ClaveSecreta2026"
inicio = len(envios_make)
r = admin_web.post("/admin/usuarios/nuevo", data={
    "csrf_token": token(admin_web, "/admin/usuarios/nuevo"), "nombre": "Lucia Herrera",
    "correo": "lucia.herrera@sgds.com", "rol": Rol.CONDUCTOR, "cliente_id": "0",
    "contrasena": CLAVE_NUEVA, "activo": "y"})
check(r.status_code == 302, "crear una cuenta en pantalla")
r = registros(accion="Crear usuario")
check(len(r) == 1 and r[0].origen == "pantalla" and r[0].usuario_id == admin_id and r[0].call_id is None,
      "queda en la auditoria con origen pantalla")
if r:
    with app.app_context():
        lucia_id = db.session.query(Usuario).filter_by(correo="lucia.herrera@sgds.com").one().id
    check(r[0].entidad == "usuario" and r[0].entidad_id == str(lucia_id)
          and r[0].detalle_dict == {"nombre": "Lucia Herrera", "correo": "lucia.herrera@sgds.com",
                                    "rol": "Conductor", "activo": True},
          f"con los datos de la cuenta, sin contrasena {r[0].detalle_dict}")

r = admin_web.post(f"/admin/usuarios/{lucia_id}/editar", data={
    "csrf_token": token(admin_web, f"/admin/usuarios/{lucia_id}/editar"), "nombre": "Lucia Herrera Gil",
    "correo": "lucia.herrera@sgds.com", "rol": Rol.CONDUCTOR, "cliente_id": "0",
    "contrasena": "OtraClave2026", "activo": "y"})
r = registros(accion="Editar usuario")
check(len(r) == 1 and r[0].detalle_dict.get("nombre") == ["Lucia Herrera", "Lucia Herrera Gil"]
      and r[0].detalle_dict.get("contrasena") == "(no se registra)",
      f"editar registra el cambio de nombre y que cambio la contrasena, sin ella {r[0].detalle_dict if r else None}")

html = admin_web.get("/admin/usuarios/").data.decode()
r = admin_web.post(f"/admin/usuarios/{lucia_id}/restablecer",
                   data={"csrf_token": token(admin_web, "/admin/usuarios/")}, follow_redirects=True)
temporal = re.search(r"Contrasena temporal de lucia.herrera@sgds.com: (\S+)", r.data.decode())
r = registros(accion="Restablecer contraseña")
check(len(r) == 1 and temporal and temporal.group(1) not in (r[0].detalle or "") + r[0].resumen,
      "restablecer queda en la auditoria sin la contrasena temporal")

admin_web.post(f"/admin/usuarios/{lucia_id}/alternar-estado",
               data={"csrf_token": token(admin_web, "/admin/usuarios/")})
r = registros(accion="Desactivar usuario")
check(len(r) == 1 and r[0].detalle_dict.get("activo") == [True, False], "desactivar queda en la auditoria")

todo = json.dumps(envios_make[inicio:], ensure_ascii=False)
notificaciones.esperar_envios()
todo = json.dumps(envios_make[inicio:], ensure_ascii=False)
check(CLAVE_NUEVA not in todo and "OtraClave2026" not in todo
      and (temporal is None or temporal.group(1) not in todo),
      "ninguna contrasena de pantalla viaja en un evento")
check(not eventos("credencial_usuario", inicio),
      "en pantalla no se envia credencial_usuario: la contrasena se muestra una vez, como antes")
check(len(eventos("accion_asistente", inicio)) == 4, "cada accion sobre usuarios envia accion_asistente")


print("\n== 5. Configuracion en pantalla ==")
url = "/admin/configuracion/"
admin_web.post(url + "centro", data={"csrf_token": token(admin_web, url), "cd_nombre": "CD Norte",
                                     "cd_latitud": "4.75", "cd_longitud": "-74.05"})
r = registros(accion="Editar centro de distribución")
check(len(r) == 1 and r[0].detalle_dict.get("nombre", [None, None])[1] == "CD Norte"
      and r[0].detalle_dict.get("latitud", [None, None])[1] == 4.75,
      "editar el centro registra antes y despues")
admin_web.post(url + "motivos", data={"csrf_token": token(admin_web, url), "nombre": "Vehiculo varado"})
with app.app_context():
    motivo_id = db.session.query(MotivoFallo).filter_by(nombre="Vehiculo varado").one().id
admin_web.post(url + f"motivos/{motivo_id}/renombrar",
               data={"csrf_token": token(admin_web, url), "nombre": "Vehiculo averiado"})
admin_web.post(url + f"motivos/{motivo_id}/estado", data={"csrf_token": token(admin_web, url)})
acciones = [x.accion for x in registros(entidad="motivo_fallo")]
check(acciones == ["Agregar motivo de fallo", "Renombrar motivo de fallo", "Desactivar motivo de fallo"],
      f"agregar, renombrar y desactivar motivos quedan en la auditoria {acciones}")
check(all(x.origen == "pantalla" and x.usuario_id == admin_id for x in registros(entidad="configuracion")
          + registros(entidad="motivo_fallo")), "todos con origen pantalla y el administrador")
antes = total_registros()
admin_web.post(url + "motivos", data={"csrf_token": token(admin_web, url), "nombre": ""})
check(total_registros() == antes, "un cambio rechazado por las reglas no deja registro")


print("\n== 6. Evento credencial_usuario ==")
inicio = len(envios_make)
antes = total_registros()
with app.test_request_context():
    lucia = db.session.get(Usuario, lucia_id)
    admin = db.session.get(Usuario, admin_id)
    notificaciones.aviso_credencial_usuario(lucia, "Generada123", notificaciones.ACCION_CREACION, admin)
    db.session.commit()
e = eventos("credencial_usuario", inicio)
check(len(e) == 1, "se envia credencial_usuario")
if e:
    e = e[0]
    check(set(e) == {"tipo", "nombre", "correo", "contrasena", "url_acceso", "creado_por", "accion",
                     "hora", "correo_destino"} | CAMPOS_ACTOR, f"campos {sorted(e)}")
    check(e["correo_destino"] == "lucia.herrera@sgds.com" == e["correo"],
          "va al correo de inicio de sesion del propio usuario, no a operaciones")
    check(e["url_acceso"] == URL_PUBLICA and e["accion"] == "creación"
          and e["creado_por"] == f"{admin_nombre} (Administrador)" and e["contrasena"] == "Generada123",
          "con la URL de acceso, la accion y quien la hizo")
check(total_registros() == antes and not eventos("accion_asistente", inicio),
      "la credencial no se guarda en la auditoria ni sale en otro evento")


print("\n== 7. Bitacora ==")
r = admin_web.get("/admin/bitacora/")
html = r.data.decode()
check(r.status_code == 200 and "Ajustar inventario" in html and "Crear usuario" in html,
      "el administrador ve la bitacora")
check("Bitacora" in admin_web.get("/admin/").data.decode(), "con enlace en el menu del administrador")
gestor_web = sesion("despachador@sgds.com", "Despacho123*")
check(gestor_web.get("/admin/bitacora/").status_code == 403, "el gestor no la ve (403)")
check(sesion("conductor1@sgds.com", "Conductor123*").get("/admin/bitacora/").status_code == 403,
      "el conductor tampoco")

def filas(**params):
    html = admin_web.get("/admin/bitacora/", query_string=params).data.decode()
    cuerpo = html.split("<tbody>")[1].split("</tbody>")[0] if "<tbody>" in html else ""
    return cuerpo.count("<tr>")

check(filas(origen="voz") == len(registros(origen="voz")) > 0, "filtra por origen voz")
check(filas(origen="pantalla") == len(registros(origen="pantalla")), "filtra por origen pantalla")
check(filas(accion="Crear usuario") == 1, "filtra por accion")
check(filas(usuario=gestor_id) == len(registros(usuario_id=gestor_id)) == 2, "filtra por usuario")
hoy_iso = ahora().date().isoformat()
check(filas(desde=hoy_iso, hasta=hoy_iso) == min(total_registros(), 50), "filtra por fecha (hoy)")
manana = (ahora().date() + timedelta(days=1)).isoformat()
check(filas(desde=manana) == 0 and "No hay registros con estos filtros" in
      admin_web.get("/admin/bitacora/", query_string={"desde": manana}).data.decode(),
      "un rango sin registros lo dice")
check("contrasena: (no se registra)" in admin_web.get("/admin/bitacora/",
      query_string={"accion": "Editar usuario"}).data.decode(), "el detalle se ve en texto plano")


print("\n" + "=" * 55)
print("RESULTADO:", "TODAS LAS PRUEBAS PASARON" if not fallos else f"{len(fallos)} FALLAS")
sys.exit(1 if fallos else 0)
