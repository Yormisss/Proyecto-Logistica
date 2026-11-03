"""Voz y seguridad: Gaby con los roles y permisos configurables, la llamada
atada a la sesion web, los valores descifrados y las exclusiones nuevas.

- Cada funcion de voz exige el mismo permiso que su pantalla, con el mismo
  `Usuario.puede`: quitarle un permiso a un rol o a una cuenta (tambien por
  voz) lo quita por voz.
- Roles, permisos de una cuenta, exigencia de cambio de contrasena, intentos
  de acceso y errores del sistema, por voz y con confirmacion en el servidor.
- Una llamada abierta deja de servir si la cuenta se desactiva, se bloquea por
  intentos, debe cambiar la contrasena o su sesion web deja de ser la vigente
  (otro navegador, restablecimiento). La inactividad no la corta: la voz y el
  latido del navegador cuentan como actividad.
- Busqueda por documento, telefono y NIT, respuestas y avisos a Make con los
  valores descifrados; la auditoria nunca guarda textos cifrados ni contrasenas.
- Lo que queda fuera de la voz, con su motivo.

Corre sin internet: Retell y Make se simulan.
"""

import json
import pathlib
import re
import sys
from datetime import timedelta
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _preparar  # noqa: E402  Primero: base de pruebas, sin Make, Retell ni red externa.
from retell.lib.webhook_auth import symmetric
from sqlalchemy import text

from run import app
from app.asistentes import cobertura
from app.controllers.asistente_api import FUNCIONES
from app.extensions import db
from app.models import (Auditoria, Cliente, ErrorSistema, RolAcceso, SesionAsistente, Usuario,
                        VIGENCIA_SESION)
from app.permisos import permiso_voz
from app.services import acceso, notificaciones
from app.services.asistente import (MOTIVO_BLOQUEADA, MOTIVO_CAMBIO_CONTRASENA, MOTIVO_INACTIVA,
                                    MOTIVO_OTRA_SESION, huella_sesion)
from app.services.auditoria import OCULTO, limpiar
from app.services.configuracion import editar_inactividad
from app.services.permisos import permisos_efectivos
from app.tiempo import ahora

fallos = []
def check(c, m):
    print(("  OK   " if c else "  FALLA") + f" {m}")
    if not c: fallos.append(m)

_preparar.reiniciar_base()

CLAVE = "clave-retell-seguridad"
OPERACIONES = "operaciones@sgds.invalid"
URL_MAKE = "https://hook.make.invalid/sgds"
URL_CREDENCIALES = "https://hook.make.invalid/credenciales"
app.config.update(RETELL_API_KEY=CLAVE, MAKE_WEBHOOK_URL=URL_MAKE, MAKE_WEBHOOK_KEY="",
                  MAKE_WEBHOOK_CREDENCIALES_URL=URL_CREDENCIALES, CORREO_OPERACIONES=OPERACIONES,
                  URL_PUBLICA="https://sgds.ejemplo.com", WTF_CSRF_ENABLED=False)

envios = []
notificaciones.requests.post = lambda url, json=None, timeout=None, headers=None: (
    envios.append((url, json)), SimpleNamespace(raise_for_status=lambda: None))[1]

CIFRADO = re.compile(r"gAAAAA[A-Za-z0-9_\-]{60,}")


# ---- Utilidades ----
retell = app.test_client()
def funcion(ruta, call_id, args=None):
    cuerpo = json.dumps({"call": {"call_id": call_id}, "args": args or {}}, ensure_ascii=False)
    r = retell.post(f"/api/asistente/{ruta}", data=cuerpo.encode(),
                    headers={"Content-Type": "application/json",
                             "X-Retell-Signature": symmetric["sign"](cuerpo, CLAVE)})
    return r.status_code, (r.get_json(silent=True) or {})

def dice(ruta, call_id, args=None):
    return funcion(ruta, call_id, args)[1].get("mensaje", "")

def confirma(ruta, call_id, args):
    resumen = dice(ruta, call_id, args)
    if not resumen.endswith("¿confirmas?"):
        return resumen, None
    return resumen, dice(ruta, call_id, {**args, "confirmar": True})

def llamada(correo, call_id, con_huella=True):
    """Sesion del asistente como la crea iniciar_llamada (con la huella de la sesion web)."""
    with app.app_context():
        u = db.session.query(Usuario).filter_by(correo=correo).one()
        db.session.add(SesionAsistente(call_id=call_id, usuario_id=u.id, rol=u.rol,
                                       vence_en=ahora() + VIGENCIA_SESION,
                                       huella_sesion=huella_sesion(u) if con_huella else None))
        db.session.commit()

def navegador(correo, clave):
    c = app.test_client()
    c.post("/auth/login", data={"correo": correo, "contrasena": clave})
    return c

def usuario(correo):
    with app.app_context():
        u = db.session.query(Usuario).filter_by(correo=correo).one()
        db.session.expunge(u)
        return u

def eventos(tipo, desde=0):
    notificaciones.esperar_envios()
    return [e for _, e in envios[desde:] if e.get("tipo") == tipo]

def latido(cliente, call_id):
    r = cliente.get(f"/asistente/latido?call_id={call_id}")
    return r.status_code, (r.get_json(silent=True) if r.is_json else None), r.headers.get("Location", "")

ADMIN, GESTOR, ANDRES, DIEGO, CLIENTE = "admin@sgds.com", "despachador@sgds.com", \
    "conductor1@sgds.com", "conductor2@sgds.com", "cliente@sgds.com"
A, G = "call_seg_admin", "call_seg_gestor"
llamada(ADMIN, A)
llamada(GESTOR, G)


print("\n== 1. Cada funcion de voz exige el permiso de su pantalla ==")
rutas = cobertura.rutas_post_de_pantalla(app)
distintos = []
for endpoint, entrada in sorted(cobertura.COBERTURA.items()):
    pantalla = getattr(app.view_functions.get(endpoint), "permiso_post", None)
    for referencia in entrada.voz:
        espacio, nombre, operacion = cobertura.separar(referencia)
        f = FUNCIONES[(espacio, nombre)]
        modifica = f.operaciones[operacion].modifica if operacion else f.accion
        if permiso_voz(espacio, nombre, modifica) != pantalla:
            distintos.append((endpoint, referencia, pantalla, permiso_voz(espacio, nombre, modifica)))
check(not distintos, f"toda operacion de voz exige el mismo permiso que su ruta de pantalla {distintos}")
check(all(permiso_voz(f.espacio, f.nombre, False) and permiso_voz(f.espacio, f.nombre, True)
          for f in FUNCIONES.values()), "toda funcion de voz exige un permiso, al consultar y al cambiar")
nuevas = {("admin", "roles"): "roles.gestionar", ("admin", "permisos"): "usuarios.gestionar",
          ("admin", "contrasenas"): "contrasenas.gestionar", ("admin", "errores"): "errores.ver"}
check(all(FUNCIONES[k].roles == ("ADMIN",) and permiso_voz(*k, True) == p for k, p in nuevas.items()),
      "roles, permisos, contrasenas y errores: solo el administrador, con el permiso de su pantalla")


print("\n== 2. Roles por voz ==")
r = dice("admin/roles", A, {"operacion": "consultar"})
check("Gestor logistico" in r and "Administrador" in r, f"consultar lista los roles: {r[:90]}")
r = dice("admin/roles", A, {"operacion": "crear", "nombre": "Auditor", "permisos": ["gestionar roles"]})
check("no se reparte" in r and not r.endswith("¿confirmas?"), f"gestionar roles no se reparte: {r}")
resumen, final = confirma("admin/roles", A, {"operacion": "crear", "nombre": "Auditor",
                                             "permisos": ["ver la bitacora", "ver el tablero"]})
with app.app_context():
    auditor = db.session.query(RolAcceso).filter_by(nombre="Auditor").one_or_none()
    permisos_auditor = set(auditor.permisos) if auditor else set()
check(resumen.startswith("Voy a crear el rol Auditor con 2 permisos") and final == "Listo, creé el rol Auditor."
      and permisos_auditor == {"bitacora.ver", "tablero.ver"}, f"crear un rol con sus permisos: {resumen}")
_, final = confirma("admin/roles", A, {"operacion": "agregar-permiso", "rol": "auditor",
                                       "permiso": "ver el registro de errores"})
with app.app_context():
    check("errores.ver" in db.session.query(RolAcceso).filter_by(nombre="Auditor").one().permisos,
          "agregar-permiso suma el permiso al rol")
check("no se renombra" in dice("admin/roles", A, {"operacion": "editar", "rol": "conductor", "nombre": "Chofer"}),
      "un rol del sistema no se renombra por voz")
check("no se edita" in dice("admin/roles", A, {"operacion": "agregar-permiso", "rol": "administrador",
                                                "permiso": "ver pedidos"}),
      "el rol de administrador no se edita, como en pantalla")

# Quitarle un permiso al rol del gestor tambien lo quita por voz.
args_entrada = {"operacion": "entrada", "producto": "SKU-1001", "cantidad": 5, "motivo": "Prueba"}
check(dice("gestor/productos", G, args_entrada).endswith("¿confirmas?"), "el gestor registra entradas por voz")
_, final = confirma("admin/roles", A, {"operacion": "quitar-permiso", "rol": "gestor",
                                       "permiso": "gestionar inventario"})
check(final and final.startswith("Listo, actualicé el rol Gestor logistico"), f"quitar-permiso: {final}")
r = dice("gestor/productos", G, args_entrada)
check("No tienes el permiso «Gestionar inventario" in r, f"sin el permiso, el gestor ya no puede por voz: {r}")
r = dice("gestor/productos", G, {**args_entrada, "operacion": "  ENTRADA "})
check("No tienes el permiso" in r, "la operacion se compara normalizada: no hay forma de saltar el permiso")
check("No tienes el permiso" not in dice("gestor/stock-producto", G, {"producto": "SKU-1001"}),
      "pero sigue consultando el stock (ver inventario)")
confirma("admin/roles", A, {"operacion": "agregar-permiso", "rol": "gestor", "permiso": "gestionar inventario"})
check(dice("gestor/productos", G, args_entrada).endswith("¿confirmas?"), "devuelto el permiso, vuelve a poder")
_, final = confirma("admin/roles", A, {"operacion": "eliminar", "rol": "auditor"})
with app.app_context():
    check(final == "Listo, eliminé el rol Auditor."
          and db.session.query(RolAcceso).filter_by(nombre="Auditor").count() == 0, "eliminar un rol")
    registros = db.session.query(Auditoria).filter_by(entidad="rol", origen="voz").count()
check(registros >= 5, f"cada accion confirmada sobre roles queda en la auditoria, por voz ({registros})")


print("\n== 3. Permisos de una cuenta por voz ==")
args_buscar = {"pedido": "pedido 1 de hoy"}
check("No tienes el permiso" not in dice("gestor/buscar-pedido", G, args_buscar), "el gestor busca pedidos")
_, final = confirma("admin/permisos", A, {"operacion": "denegar", "usuario": "carlos", "permiso": "ver pedidos"})
check(final and final.startswith("Listo, actualicé los permisos de Carlos Rueda"), f"denegar: {final}")
r = dice("gestor/buscar-pedido", G, args_buscar)
check("No tienes el permiso «Ver pedidos»" in r, f"denegado a la cuenta, tampoco por voz: {r}")
r = dice("admin/permisos", A, {"operacion": "consultar", "usuario": "carlos"})
check("Se le negó: Ver pedidos." in r, f"consultar dice el ajuste: {r[-80:]}")
confirma("admin/permisos", A, {"operacion": "quitar-ajuste", "usuario": "carlos", "permiso": "ver pedidos"})
check("No tienes el permiso" not in dice("gestor/buscar-pedido", G, args_buscar),
      "quitar el ajuste lo devuelve segun sus roles")
_, final = confirma("admin/permisos", A, {"operacion": "asignar-rol", "usuario": "diego", "rol": "gestor"})
with app.app_context():
    diego = db.session.query(Usuario).filter_by(correo=DIEGO).one()
    con_rol = "pedidos.ver" in permisos_efectivos(diego) and [r.nombre for r in diego.roles_adicionales]
check(final and con_rol == ["Gestor logistico"], f"asignar-rol suma un rol adicional: {final}")
confirma("admin/permisos", A, {"operacion": "quitar-rol", "usuario": "diego", "rol": "gestor"})
with app.app_context():
    check(not db.session.query(Usuario).filter_by(correo=DIEGO).one().roles_adicionales, "quitar-rol")
check("tienen todos los permisos" in dice("admin/permisos", A, {"operacion": "conceder", "usuario": "admin@sgds.com",
                                                                 "permiso": "ver pedidos"}),
      "un administrador no lleva ajustes, como en pantalla")
check("no se reparte" in dice("admin/permisos", A, {"operacion": "conceder", "usuario": "carlos",
                                                    "permiso": "gestionar usuarios"}),
      "gestionar usuarios no se concede a nadie")
with app.app_context():
    registro = db.session.query(Auditoria).filter_by(accion="Denegar permiso a usuario").one()
check(registro.origen == "voz" and registro.call_id == A, "los ajustes por voz quedan en la auditoria")


print("\n== 4. Contrasenas, intentos de acceso y errores por voz ==")
D = "call_seg_diego"
llamada(DIEGO, D)
check(funcion("conductor/mi-ruta", D)[0] == 200, "Diego usa su llamada")
_, final = confirma("admin/contrasenas", A, {"operacion": "exigir-cambio", "usuario": "diego"})
check(final == "Listo, Diego Pardo deberá cambiar su contraseña.", f"exigir-cambio: {final}")
estado, cuerpo = funcion("conductor/mi-ruta", D)
check(estado == 403 and cuerpo.get("mensaje") == MOTIVO_CAMBIO_CONTRASENA,
      "con el cambio exigido, su llamada abierta deja de servir y Gaby dice por que")
check("ya debe cambiar" in dice("admin/contrasenas", A, {"operacion": "exigir-cambio", "usuario": "diego"}),
      "no se exige dos veces")
confirma("admin/contrasenas", A, {"operacion": "quitar-exigencia", "usuario": "diego"})
check(funcion("conductor/mi-ruta", D)[0] == 200, "quitada la exigencia, la llamada vuelve a servir")
check("Solo cada administrador" in dice("admin/contrasenas", A, {"operacion": "exigir-cambio", "usuario": "admin@sgds.com"})
      or "propia" in dice("admin/contrasenas", A, {"operacion": "exigir-cambio", "usuario": "admin@sgds.com"}),
      "la de un administrador no se exige, como en pantalla")
_, final = confirma("admin/contrasenas", A, {"operacion": "exigir-cambio-masivo", "rol": "conductor"})
with app.app_context():
    conductores = db.session.query(Usuario).filter_by(rol="CONDUCTOR").all()
    todos = all(u.debe_cambiar_contrasena for u in conductores)
    gestor_libre = not db.session.query(Usuario).filter_by(correo=GESTOR).one().debe_cambiar_contrasena
    for u in conductores:
        u.debe_cambiar_contrasena = False
    db.session.commit()
check(final and todos and gestor_libre, f"exigir-cambio-masivo solo a los conductores: {final}")
with app.app_context():
    for _ in range(2):
        acceso.registrar_fallo(ANDRES)
r = dice("admin/contrasenas", A, {"operacion": "intentos"})
check("Andres Molina, 2 de 5" in r, f"intentos lista las cuentas con fallos: {r}")
r = dice("admin/contrasenas", A, {"operacion": "consultar", "usuario": "andres"})
check("2 intentos fallidos" in r and "no tiene cambio pendiente" in r, f"consultar una cuenta: {r}")
with app.app_context():
    acceso.registrar_exito(ANDRES)
    db.session.add(ErrorSistema(metodo="GET", ruta="/admin/prueba", tipo="ZeroDivisionError",
                                mensaje="division by zero", traza="Traceback..."))
    db.session.commit()
    ultimo = db.session.query(ErrorSistema).order_by(ErrorSistema.id.desc()).first().id
r = dice("admin/errores", A)
check(f"número {ultimo}" in r and "ZeroDivisionError" in r, f"errores lee los recientes: {r[:120]}")
check("division by zero" in dice("admin/errores", A, {"numero": ultimo}), "y el detalle de uno por su numero")


print("\n== 5. La llamada sigue a la sesion web que la abrio ==")
web = navegador(ANDRES, "Conductor123*")
C = "call_seg_andres"
llamada(ANDRES, C)
check(funcion("conductor/mi-ruta", C)[0] == 200, "la llamada abierta desde la sesion vigente sirve")
estado, datos, _ = latido(web, C)
check(estado == 200 and datos == {"vigente": True, "mensaje": ""}, f"el latido la confirma vigente: {datos}")
check(latido(web, A)[1]["vigente"] is False, "el latido no confirma la llamada de otra cuenta")

# Inactividad: hablar con Gaby es actividad, aunque no se toque la pantalla.
with app.app_context():
    editar_inactividad(1, usuario(ADMIN).id)
    db.session.commit()
    db.session.execute(text("UPDATE usuarios SET sesion_actividad_en = :t WHERE correo = :c"),
                       {"t": ahora() - timedelta(minutes=10), "c": ANDRES})
    db.session.commit()
check(funcion("conductor/mi-ruta", C)[0] == 200, "tras 10 minutos sin pantalla, la voz no se corta")
check((ahora() - usuario(ANDRES).sesion_actividad_en).total_seconds() < 30,
      "y cada funcion de voz renueva la actividad de la sesion web")
check(web.get("/auth/sesion").get_json()["restante"] > 30, "asi la pantalla tampoco vuelve al login")
with app.app_context():
    db.session.execute(text("UPDATE usuarios SET sesion_actividad_en = :t WHERE correo = :c"),
                       {"t": ahora() - timedelta(seconds=50), "c": ANDRES})
    db.session.commit()
latido(web, C)
check(web.get("/auth/sesion").get_json()["restante"] > 50, "el latido del navegador tambien cuenta como actividad")
with app.app_context():
    editar_inactividad(30, usuario(ADMIN).id)
    db.session.commit()

# Cambiar la propia contrasena en la misma ventana no corta la llamada.
r = web.post("/auth/perfil", data={"contrasena_actual": "Conductor123*", "contrasena_nueva": "Nueva#Clave2026"})
check(r.status_code == 302 and funcion("conductor/mi-ruta", C)[0] == 200,
      "cambiar la propia contrasena renueva la sesion y la llamada la sigue")

# Entrar en otro navegador cierra la sesion anterior y su llamada.
otro = navegador(ANDRES, "Nueva#Clave2026")
estado, cuerpo = funcion("conductor/mi-ruta", C)
check(estado == 403 and cuerpo.get("mensaje") == MOTIVO_OTRA_SESION,
      "al entrar en otro navegador, la llamada del anterior deja de servir")
estado, datos, destino = latido(web, C)
check(estado == 302 and "/auth/login" in destino, "y el latido de la ventana anterior la lleva al login")
C2 = "call_seg_andres_2"
llamada(ANDRES, C2)
check(funcion("conductor/mi-ruta", C2)[0] == 200, "la llamada del navegador nuevo sirve")

# Bloqueo por intentos fallidos: la llamada abierta deja de servir.
for _ in range(5):
    app.test_client().post("/auth/login", data={"correo": ANDRES, "contrasena": "equivocada"})
estado, cuerpo = funcion("conductor/mi-ruta", C2)
check(estado == 403 and cuerpo.get("mensaje") == MOTIVO_BLOQUEADA,
      "con el login bloqueado por intentos, la llamada abierta deja de servir")
estado, datos, _ = latido(otro, C2)
check(datos == {"vigente": False, "mensaje": MOTIVO_BLOQUEADA}, "el latido avisa al navegador para colgar")
with app.app_context():
    acceso.registrar_exito(ANDRES)
check(funcion("conductor/mi-ruta", C2)[0] == 200, "cumplido el bloqueo, la llamada vuelve a servir")

# Restablecer la contrasena (por voz) cierra las sesiones y la llamada.
confirma("admin/usuarios", A, {"operacion": "restablecer-contrasena", "usuario": "andres"})
check(funcion("conductor/mi-ruta", C2)[0] == 403, "restablecer la contrasena corta la llamada abierta")

# Desactivar la cuenta corta la llamada.
llamada(DIEGO, "call_seg_diego_2")
confirma("admin/usuarios", A, {"operacion": "desactivar", "usuario": "diego"})
estado, cuerpo = funcion("conductor/mi-ruta", "call_seg_diego_2")
check(estado == 403 and cuerpo.get("mensaje") == MOTIVO_INACTIVA, "una cuenta desactivada no sigue en la llamada")
confirma("admin/usuarios", A, {"operacion": "activar", "usuario": "diego"})

llamada(GESTOR, "call_seg_antigua", con_huella=False)
check(funcion("gestor/resumen-dia", "call_seg_antigua")[0] == 200,
      "una llamada abierta antes de la migracion m013 (sin huella) se valida como antes")
estado, cuerpo = funcion("gestor/resumen-dia", "call_inexistente")
check(estado == 403 and cuerpo.get("error") == "Llamada no autorizada.", "un call_id desconocido: 403")


print("\n== 6. Busqueda, respuestas y avisos con los valores descifrados ==")
with app.app_context():
    crudo = db.session.execute(text("SELECT documento, telefono FROM usuarios WHERE correo = :c"),
                               {"c": DIEGO}).one()
check(crudo[0].startswith("gAAAAA") and crudo[1].startswith("gAAAAA"),
      "el documento y el telefono estan cifrados en la base")
for dicho in ("79876543", "79.876.543", "3209876543", "+57 320 987 6543"):
    r = dice("admin/usuarios", A, {"operacion": "consultar", "usuario": dicho})
    check(r.startswith("Diego Pardo es conductor"), f"encuentra la cuenta por documento o telefono: {dicho}")
r = dice("admin/usuarios", A, {"operacion": "consultar", "usuario": "79876543"})
check("documento terminado en 6543 y teléfono terminado en 6543" in r and not CIFRADO.search(r)
      and "79876543" not in r, f"Gaby lee solo los ultimos digitos, ya descifrados: {r}")
check("No encontré" in dice("admin/usuarios", A, {"operacion": "consultar", "usuario": "11111111"}),
      "un numero que no es de nadie no encuentra nada")
for dicho in ("900123456-1", "9001234561", "900123456"):
    r = dice("gestor/clientes", G, {"operacion": "consultar", "cliente": dicho})
    check(r.startswith("Supermercado El Portal: NIT 900123456-1"), f"encuentra el cliente por NIT: {dicho}")

with app.app_context():
    portal = db.session.query(Cliente).filter_by(nombre="Supermercado El Portal").one()
    portal.telefono = "6015551234"
    db.session.commit()
check(dice("gestor/clientes", G, {"operacion": "consultar", "cliente": "601 555 1234"})
      .startswith("Supermercado El Portal"), "y por su telefono")
llamada(CLIENTE, "call_seg_cliente")
inicio = len(envios)
confirma("cliente/solicitar-contacto", "call_seg_cliente", {"motivo": "Cambiar la ventana de entrega"})
solicitud = eventos("solicitud_contacto", inicio)
check(solicitud and solicitud[0]["telefono"] == "6015551234" and not CIFRADO.search(json.dumps(solicitud)),
      "solicitud_contacto lleva el telefono legible del cliente")

inicio = len(envios)
args_nuevo = {"operacion": "crear", "nombre": "Luz Pineda", "correo": "luz@sgds.com", "rol": "conductor",
              "documento": "52998877", "telefono": "3015550000"}
resumen, final = confirma("admin/usuarios", A, args_nuevo)
credencial = eventos("credencial_usuario", inicio)
check(credencial and credencial[0]["nombre"] == "Luz Pineda" and credencial[0]["correo"] == "luz@sgds.com"
      and not CIFRADO.search(json.dumps(credencial)), "credencial_usuario lleva los datos legibles")
check(credencial and credencial[0]["debe_cambiar_contrasena"] is True and "Es temporal" in (final or "")
      and "es temporal" in resumen, "la cuenta creada por voz debe cambiar la contrasena; Gaby lo dice")
clave_luz = credencial[0]["contrasena"] if credencial else "x" * 12
luz = navegador("luz@sgds.com", clave_luz)
r = luz.get("/conductor/mi-ruta")
check(r.status_code == 302 and "/auth/perfil" in r.headers.get("Location", ""),
      "al entrar con la temporal, solo puede ir a cambiarla")
confirma("admin/usuarios", A, {"operacion": "editar", "usuario": "luz", "telefono": "3017774444"})

with app.app_context():
    filas = db.session.query(Auditoria).all()
    textos = [(a.resumen or "") + " " + (a.detalle or "") for a in filas]
    de_luz = [t for a, t in zip(filas, textos) if a.entidad == "usuario" and "Luz Pineda" in t]
check(not any(CIFRADO.search(t) for t in textos), "ningun registro de la auditoria guarda un texto cifrado")
check(not any(clave_luz in t or "Nueva#Clave2026" in t or "Conductor123*" in t for t in textos),
      "ni una contrasena")
check(de_luz and not any("3017774444" in t or "52998877" in t for t in de_luz)
      and any("******4444" in t for t in de_luz), "el documento y el telefono de la cuenta van enmascarados")
with app.app_context():
    from app.cifrado import cifrar
    token = cifrar("1023456789")
check(limpiar({"x": token, "y": f"antes {token} despues"}) == {"x": OCULTO, "y": f"antes {OCULTO} despues"},
      "si un texto cifrado llegara a la auditoria, se oculta")


print("\n== 7. Lo que queda fuera de la voz ==")
fuera = {
    "auth.recuperar": "Olvidé mi contraseña", "auth.restablecer": "contraseñas nunca se dicen",
    "auth.perfil": "propia contraseña", "auth.cambiar_correo": "identidad de la cuenta",
    "usuarios.contrasena": "contraseñas nunca se dicen", "pedidos.importar": "archivo",
    "pedidos.confirmar_importacion": "archivo", "errores.probar": "diagnóstico",
}
for endpoint, motivo in fuera.items():
    check(endpoint in rutas and motivo in cobertura.COBERTURA[endpoint].excluida,
          f"{endpoint} queda fuera de la voz, con su motivo")
sensibles = sorted(f"{f.espacio}/{f.nombre}.{p}" for f in FUNCIONES.values() for p in f.parametros
                   if re.search(r"contrasena|clave|password|token", p))
check(not sensibles, f"ninguna funcion de voz recibe una contrasena {sensibles}")
check("solo lo cambia un administrador en pantalla" in dice("admin/usuarios", A, {
          "operacion": "editar", "usuario": "luz", "correo": "otra@sgds.com"}),
      "el correo de inicio de sesion no se cambia por voz")
readme = (_preparar.RAIZ / "README.md").read_text(encoding="utf-8")
tabla = readme[readme.find("Lo que queda fuera de la voz"):][:6000]
for frase in ("Olvidé mi contraseña", "propia contraseña", "propio correo", "Subir o revisar archivos",
              "Escribir una contraseña", "Provocar un error"):
    check(frase in tabla, f"el README lista la exclusion: {frase}")


print("\n" + "=" * 55)
print("RESULTADO:", "TODAS LAS PRUEBAS PASARON" if not fallos else f"{len(fallos)} FALLAS")
for f in fallos: print("   - " + f)
sys.exit(1 if fallos else 0)
