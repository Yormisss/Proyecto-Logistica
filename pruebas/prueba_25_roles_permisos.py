"""Roles con permisos configurables (RF1 / RNF5).

Cubre los dos escenarios de la evaluacion: un rol nuevo asignado a dos
usuarios, con dos permisos distintos para cada uno, y dos roles en un mismo
usuario con la totalidad de sus permisos modificada. Ademas, el reparto por
defecto (igual al de los roles fijos), editar un rol del sistema, que la voz
respete los permisos, las reglas contra la escalada de privilegios, eliminar
un rol, la auditoria y la migracion m012.
"""

import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _preparar  # noqa: E402  Primero: base de pruebas, sin Make, Retell ni red externa.
_preparar.reiniciar_base()
from retell.lib.webhook_auth import symmetric
from sqlalchemy import inspect

from run import app
from app.controllers.asistente_api import FUNCIONES
from app.extensions import db
from app.models import Auditoria, RolAcceso, SesionAsistente, Usuario, VIGENCIA_SESION
from app.permisos import ASIGNABLES, PERMISOS, PERMISOS_POR_DEFECTO, permiso_voz
from app.tiempo import ahora

fallos = []
def check(cond, msg):
    print(("  OK   " if cond else "  FALLA") + f" {msg}")
    if not cond: fallos.append(msg)

app.config["WTF_CSRF_ENABLED"] = False


def sesion(correo, clave):
    c = app.test_client()
    c.post("/auth/login", data={"correo": correo, "contrasena": clave})
    return c


def puede_ver(cliente, ruta):
    return cliente.get(ruta).status_code == 200


def usuario(correo):
    with app.app_context():
        u = db.session.query(Usuario).filter_by(correo=correo).one()
        return u.id


def rol_id(nombre):
    with app.app_context():
        return db.session.query(RolAcceso).filter_by(nombre=nombre).one().id


def alertas(r):
    return " | ".join(re.findall(r'<div class="alerta alerta-\w+">(.*?)</div>', r.data.decode()))


admin = sesion("admin@sgds.com", "Admin123*")
ID_C1, ID_C2, ID_GESTOR = (usuario("conductor1@sgds.com"), usuario("conductor2@sgds.com"),
                           usuario("despachador@sgds.com"))

print("\n== 1. Reparto por defecto: igual a los roles fijos ==")
with app.app_context():
    sistema = {r.clave_sistema: r for r in db.session.query(RolAcceso).filter(RolAcceso.clave_sistema.isnot(None))}
check(set(sistema) == {"ADMIN", "DESPACHADOR", "CONDUCTOR", "CLIENTE"}, "existen los cuatro roles del sistema")
gestor = sesion("despachador@sgds.com", "Despacho123*")
c1 = sesion("conductor1@sgds.com", "Conductor123*")
for ruta, quien, esperado in (("/pedidos/", gestor, 200), ("/admin/roles/", gestor, 403),
                              ("/admin/usuarios/", gestor, 403), ("/pedidos/", c1, 403),
                              ("/conductor/", c1, 200), ("/admin/roles/", admin, 200)):
    check(quien.get(ruta).status_code == esperado, f"{ruta}: {esperado}")
html = admin.get("/admin/roles/").data.decode()
check("Todos los permisos" in html and "Gestor logistico" in html, "la pantalla Roles lista los roles con su reparto")
check(all(permiso_voz(f.espacio, f.nombre, True) is not None for f in FUNCIONES.values()),
      "cada funcion de voz exige un permiso")

print("\n== 2. Escenario 5: un rol nuevo, a dos usuarios, con dos permisos distintos para cada uno ==")
r = admin.post("/admin/roles/nuevo", data={"nombre": "Supervisor de bodega",
                                           "descripcion": "Revisa la operacion sin modificarla",
                                           "permisos": ["pedidos.ver", "inventario.ver"]},
               follow_redirects=True)
check("Rol Supervisor de bodega creado" in alertas(r), "el admin crea el rol con dos permisos")
SUPERVISOR = rol_id("Supervisor de bodega")
r = admin.post("/admin/roles/nuevo", data={"nombre": "  supervisor DE bodega ", "permisos": ["rutas.ver"]},
               follow_redirects=True)
check("Ya existe el rol" in alertas(r), "no se repite el nombre (sin distinguir mayusculas ni espacios)")
r = admin.post("/admin/roles/nuevo", data={"nombre": "Vacio"}, follow_redirects=True)
check("al menos un permiso" in alertas(r), "un rol nuevo necesita al menos un permiso")

for id_usuario, propios in ((ID_C1, ("clientes.ver", "analitica.ver")),
                            (ID_C2, ("rutas.ver", "bitacora.ver"))):
    r = admin.post(f"/admin/usuarios/{id_usuario}/permisos",
                   data={"roles": [str(SUPERVISOR)], **{f"ajuste-{p}": "conceder" for p in propios}},
                   follow_redirects=True)
    check("actualizados" in alertas(r), f"se asigna a la cuenta {id_usuario} con sus dos permisos propios")

c1 = sesion("conductor1@sgds.com", "Conductor123*")
c2 = sesion("conductor2@sgds.com", "Conductor123*")
for cuenta, nombre, si, no in (
    (c1, "conductor 1", ("/pedidos/", "/inventario/", "/admin/clientes/", "/admin/analitica", "/conductor/"),
     ("/rutas/", "/admin/bitacora/", "/pedidos/nuevo", "/admin/usuarios/")),
    (c2, "conductor 2", ("/pedidos/", "/inventario/", "/rutas/", "/admin/bitacora/", "/conductor/"),
     ("/admin/clientes/", "/admin/analitica", "/inventario/nuevo", "/admin/roles/")),
):
    check(all(puede_ver(cuenta, r) for r in si), f"{nombre}: ve lo del rol, lo propio y su ruta")
    check(all(cuenta.get(r).status_code == 403 for r in no), f"{nombre}: lo demas sigue prohibido (403)")
html = c1.get("/conductor/").data.decode()
check(all(f">{t}<" in html for t in ("Mi ruta", "Pedidos", "Inventario", "Clientes", "Analítica"))
      and ">Rutas<" not in html, "el menu muestra solo lo que puede abrir")
html = c1.get("/pedidos/").data.decode()
check("Nuevo pedido" not in html and "Importar CSV" not in html,
      "con solo ver pedidos no se ofrecen los botones de gestion")
html = admin.get(f"/admin/roles/{SUPERVISOR}/editar").data.decode()
check("lo tienen 2 cuenta(s)" in html and "Andres Molina" in html and "Diego Pardo" in html,
      "la ficha del rol muestra las dos cuentas")
html = admin.get("/admin/usuarios/").data.decode()
check(html.count("+ Supervisor de bodega") == 2, "la lista de usuarios muestra el rol adicional")

print("\n== 3. Escenario 8: dos roles en un usuario y la totalidad de sus permisos modificada ==")
r = admin.post("/admin/roles/nuevo", data={"nombre": "Auditor", "permisos": ["bitacora.ver", "errores.ver"]},
               follow_redirects=True)
AUDITOR = rol_id("Auditor")
r = admin.post(f"/admin/usuarios/{ID_GESTOR}/permisos",
               data={"roles": [str(SUPERVISOR), str(AUDITOR)]}, follow_redirects=True)
check("actualizados" in alertas(r), "el gestor suma dos roles: Supervisor de bodega y Auditor")
gestor = sesion("despachador@sgds.com", "Despacho123*")
check(puede_ver(gestor, "/admin/bitacora/") and puede_ver(gestor, "/admin/errores/")
      and puede_ver(gestor, "/pedidos/nuevo"), "tiene los de su rol principal y los de ambos roles")

# Todos los permisos asignables, cada uno ajustado: concede los que no tenia y deniega los que tenia.
with app.app_context():
    from app.services.permisos import permisos_efectivos
    antes = set(permisos_efectivos(db.session.get(Usuario, ID_GESTOR)))
ajustes = {f"ajuste-{p}": ("denegar" if p in antes else "conceder") for p in ASIGNABLES}
r = admin.post(f"/admin/usuarios/{ID_GESTOR}/permisos",
               data={"roles": [str(SUPERVISOR), str(AUDITOR)], **ajustes}, follow_redirects=True)
check("actualizados" in alertas(r), "se ajustan todos sus permisos en una sola pantalla")
with app.app_context():
    despues = set(permisos_efectivos(db.session.get(Usuario, ID_GESTOR)))
check(despues == set(ASIGNABLES) - antes, "cada permiso quedo invertido: la totalidad cambio")
gestor = sesion("despachador@sgds.com", "Despacho123*")
check(gestor.get("/pedidos/").status_code == 403 and gestor.get("/admin/bitacora/").status_code == 403,
      "denegar quita incluso lo que dan sus roles")
check(puede_ver(gestor, "/admin/configuracion/") and puede_ver(gestor, "/admin/rendimiento"),
      "y conceder da lo que ningun rol le daba")
r = gestor.get("/auth/login")
check(r.status_code == 302 and r.headers["Location"].endswith("/admin/rendimiento"),
      "sin el tablero, entra a la primera pagina que puede abrir (rendimiento)")
html = admin.get(f"/admin/usuarios/{ID_GESTOR}/permisos").data.decode()
check(html.count('value="denegar" selected') == len(antes) and "Auditor" in html,
      "la pantalla de permisos muestra cada ajuste y de que roles viene")
with app.app_context():
    registro = (db.session.query(Auditoria).filter_by(accion="Editar permisos de usuario")
                .order_by(Auditoria.id.desc()).first())
check(registro is not None and "denegados" in registro.detalle and "concedidos" in registro.detalle,
      "el cambio queda en la bitacora")
admin.post(f"/admin/usuarios/{ID_GESTOR}/permisos", data={})
gestor = sesion("despachador@sgds.com", "Despacho123*")
check(puede_ver(gestor, "/pedidos/") and gestor.get("/admin/bitacora/").status_code == 403,
      "quitar roles y ajustes lo devuelve a su rol principal")

print("\n== 4. Editar un rol del sistema lo cambia para todas sus cuentas, tambien por voz ==")
gestion = [p for p in PERMISOS_POR_DEFECTO["DESPACHADOR"] if p != "inventario.gestionar"]
r = admin.post(f"/admin/roles/{sistema['DESPACHADOR'].id}/editar",
               data={"nombre": "Otro nombre", "permisos": gestion}, follow_redirects=True)
check("actualizado" in alertas(r), "el admin le quita al gestor gestionar inventario")
with app.app_context():
    check(db.session.get(RolAcceso, sistema["DESPACHADOR"].id).nombre == "Gestor logistico",
          "un rol del sistema no se renombra")
gestor = sesion("despachador@sgds.com", "Despacho123*")
check(puede_ver(gestor, "/inventario/") and gestor.get("/inventario/nuevo").status_code == 403,
      "el gestor sigue viendo el inventario pero ya no crea productos")
check(gestor.post("/inventario/1", data={"tipo": "ENTRADA", "cantidad": "5", "motivo": "x"}).status_code == 403,
      "ni registra movimientos (el POST del detalle exige gestionar)")

CLAVE = "clave-retell-prueba"
app.config.update(RETELL_API_KEY=CLAVE, MAKE_WEBHOOK_URL="", CORREO_OPERACIONES="")
with app.app_context():
    db.session.add(SesionAsistente(call_id="call_permisos", usuario_id=ID_GESTOR, rol="DESPACHADOR",
                                   vence_en=ahora() + VIGENCIA_SESION))
    db.session.commit()


def voz(ruta, args):
    cuerpo = json.dumps({"call": {"call_id": "call_permisos"}, "name": ruta.split("/")[-1], "args": args},
                        ensure_ascii=False)
    r = app.test_client().post(f"/api/asistente/{ruta}", data=cuerpo.encode(),
                               headers={"Content-Type": "application/json",
                                        "X-Retell-Signature": symmetric["sign"](cuerpo, CLAVE)})
    return (r.get_json(silent=True) or {}).get("mensaje", "")


m = voz("gestor/productos", {"operacion": "entrada", "producto": "SKU-1001", "cantidad": 5, "motivo": "x"})
check("No tienes el permiso «Gestionar inventario" in m, f"por voz tampoco: {m[:90]}")
m = voz("gestor/stock-producto", {"producto": "SKU-1001"})
check("No tienes el permiso" not in m and m, "pero si consulta el stock (inventario.ver)")
admin.post(f"/admin/roles/{sistema['DESPACHADOR'].id}/editar",
           data={"permisos": sorted(PERMISOS_POR_DEFECTO["DESPACHADOR"])})
gestor = sesion("despachador@sgds.com", "Despacho123*")
check(gestor.get("/inventario/nuevo").status_code == 200, "devolverle el permiso lo restituye")

print("\n== 5. Reglas contra la escalada de privilegios ==")
r = admin.post("/admin/roles/nuevo", data={"nombre": "Superpoderes", "permisos": ["usuarios.gestionar"]},
               follow_redirects=True)
check("solo lo tienen los administradores" in alertas(r), "un rol no puede dar gestionar usuarios")
r = admin.post("/admin/roles/nuevo", data={"nombre": "Portal", "permisos": ["portal.cliente"]},
               follow_redirects=True)
check("depende del tipo de cuenta" in alertas(r), "ni el portal del cliente")
r = admin.post(f"/admin/usuarios/{ID_C1}/permisos", data={"ajuste-roles.gestionar": "conceder"},
               follow_redirects=True)
check("no se ajusta por cuenta" in alertas(r), "un ajuste fabricado tampoco da gestionar roles")
r = admin.post(f"/admin/usuarios/{ID_C1}/permisos", data={"roles": [str(sistema["ADMIN"].id)]},
               follow_redirects=True)
check("no se puede asignar" in alertas(r), "el rol de administrador no se suma como adicional")
id_cliente = usuario("cliente@sgds.com")
r = admin.post(f"/admin/usuarios/{id_cliente}/permisos", data={"roles": [str(SUPERVISOR)]},
               follow_redirects=True)
check("solo ven su portal" in alertas(r), "una cuenta de cliente no lleva roles adicionales")
cliente = sesion("cliente@sgds.com", "Cliente123*")
check(cliente.get("/pedidos/").status_code == 403, "y sigue sin ver la operacion")
id_admin = usuario("admin@sgds.com")
r = admin.post(f"/admin/usuarios/{id_admin}/permisos", data={"ajuste-pedidos.ver": "denegar"},
               follow_redirects=True)
check("tienen todos los permisos" in alertas(r) and puede_ver(admin, "/pedidos/"),
      "a un administrador no se le quitan permisos")
for rol in ("ADMIN", "CLIENTE"):
    r = admin.post(f"/admin/roles/{sistema[rol].id}/editar", data={"permisos": ["pedidos.ver"]},
                   follow_redirects=True)
    check(r.status_code == 200 and "Guardar rol" not in r.data.decode(), f"el rol {rol} no se edita")
r = admin.post(f"/admin/roles/{sistema['DESPACHADOR'].id}/eliminar", follow_redirects=True)
check("es del sistema y no se elimina" in alertas(r), "eliminar un rol del sistema se rechaza")
with app.app_context():
    check(db.session.get(RolAcceso, sistema["DESPACHADOR"].id) is not None, "y sigue existiendo")
supervisor_c1 = sesion("conductor1@sgds.com", "Conductor123*")
check(supervisor_c1.get(f"/admin/usuarios/{ID_C1}/permisos").status_code == 403,
      "nadie sin gestionar usuarios abre la pantalla de permisos, ni la propia")

print("\n== 6. Eliminar un rol creado ==")
r = admin.post(f"/admin/roles/{SUPERVISOR}/eliminar", follow_redirects=True)
check("eliminado" in alertas(r) and "2 cuenta(s) dejan de tenerlo" in alertas(r), "el admin elimina el rol")
c2 = sesion("conductor2@sgds.com", "Conductor123*")
check(c2.get("/pedidos/").status_code == 403 and puede_ver(c2, "/rutas/"),
      "sus cuentas pierden lo del rol y conservan sus ajustes propios")
with app.app_context():
    acciones = {a.accion for a in db.session.query(Auditoria).filter(Auditoria.entidad == "rol")}
check({"Crear rol", "Editar rol", "Eliminar rol"} <= acciones, "crear, editar y eliminar quedan en la bitacora")

print("\n== 7. Migracion m012 ==")
from migraciones.m012_roles_y_permisos import TABLAS, aplicar
with app.app_context():
    check(aplicar(verboso=False) == [], "sobre una base al dia no hace nada")
    db.session.remove()
    for tabla in reversed(TABLAS):
        tabla.drop(db.engine)
    check(not {t.name for t in TABLAS} & set(inspect(db.engine).get_table_names()), "la base queda como antes")
    hecho = aplicar(verboso=False)
    check(len(hecho) == 5 and "Gestor logistico" in hecho[-1], f"crea las tablas y los roles: {hecho}")
    check(aplicar(verboso=False) == [], "volver a ejecutarla no cambia nada")
gestor = sesion("despachador@sgds.com", "Despacho123*")
check(puede_ver(gestor, "/pedidos/") and gestor.get("/admin/usuarios/").status_code == 403,
      "tras migrar, cada rol tiene el acceso de siempre")


print("\n" + "=" * 55)
print("RESULTADO: " + ("TODAS LAS PRUEBAS PASARON" if not fallos else f"{len(fallos)} FALLAS"))
for f in fallos: print("   - " + f)
sys.exit(1 if fallos else 0)
