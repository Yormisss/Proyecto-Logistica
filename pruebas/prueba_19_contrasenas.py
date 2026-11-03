"""Apartado de contrasenas del administrador (RF1 / RNF5).

Cubre la gestion que hace el administrador sobre las contrasenas ajenas
—asignar, generar una temporal, exigir el cambio, uno a uno o en bloque—, el
cambio obligatorio que eso impone al usuario y el cierre de las sesiones que
quedaron abiertas con la contrasena anterior.
"""

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _preparar  # noqa: E402  Primero: base de pruebas, sin Make, Retell ni red externa.
_preparar.reiniciar_base()
from sqlalchemy import inspect, text

from run import app
from app.extensions import db
from app.models import Cliente, Rol, Usuario
from app.models.usuario import METODO_HASH

fallos = []
def check(cond, msg):
    print(("  OK   " if cond else "  FALLA") + f" {msg}")
    if not cond: fallos.append(msg)

app.config["WTF_CSRF_ENABLED"] = False


def sesion(correo, clave):
    c = app.test_client()
    c.post("/auth/login", data={"correo": correo, "contrasena": clave})
    return c


def opera(cliente, ruta):
    """True si la sesion llega a la vista, sin que la desvien al perfil o al login."""
    return cliente.get(ruta).status_code == 200


def destino(respuesta):
    return respuesta.headers.get("Location", "")


def temporal_de(html, correo):
    m = re.search(r"Contrasena temporal de " + re.escape(correo) + r": (\S+)", html)
    return m.group(1) if m else None


def usuario(correo):
    with app.app_context():
        u = db.session.query(Usuario).filter_by(correo=correo).one()
        db.session.expunge(u)
        return u


admin = sesion("admin@sgds.com", "Admin123*")
id_admin = usuario("admin@sgds.com").id
id_despachador = usuario("despachador@sgds.com").id
id_conductor1 = usuario("conductor1@sgds.com").id
id_conductor2 = usuario("conductor2@sgds.com").id
id_cliente = usuario("cliente@sgds.com").id

print("\n== 1. Solo el administrador entra al apartado ==")
r = admin.get("/admin/usuarios/contrasenas")
check(r.status_code == 200, "el admin entra al apartado de contrasenas")
for correo, clave, rol in (
    ("despachador@sgds.com", "Despacho123*", "despachador"),
    ("conductor1@sgds.com", "Conductor123*", "conductor"),
    ("cliente@sgds.com", "Cliente123*", "cliente"),
):
    c = sesion(correo, clave)
    check(c.get("/admin/usuarios/contrasenas").status_code == 403, f"el {rol} recibe 403 en el apartado")
    check(c.get(f"/admin/usuarios/{id_conductor2}/contrasena").status_code == 403,
          f"el {rol} no puede asignar contrasenas")
    check(c.post(f"/admin/usuarios/{id_conductor2}/alternar-exigencia").status_code == 403,
          f"el {rol} no puede exigir cambios")
check(app.test_client().get("/admin/usuarios/contrasenas").status_code == 302,
      "un anonimo es redirigido al login")
check(b'href="/admin/usuarios/contrasenas"' in admin.get("/admin/").data,
      "el apartado aparece en la navegacion del admin")
check(b'href="/admin/usuarios/contrasenas"' not in
      sesion("despachador@sgds.com", "Despacho123*").get("/admin/").data,
      "y no en la del despachador")

print("\n== 2. El apartado muestra el estado, nunca la contrasena ni el hash ==")
html = r.data.decode()
with app.app_context():
    hashes = [u.contrasena_hash for u in db.session.query(Usuario).all()]
    total = len(hashes)
check(not any(h in html for h in hashes), "ningun hash aparece en la pagina")
check(not any(h.split("$")[1] in html for h in hashes), "ni siquiera la sal")
check("Admin123*" not in html and "Conductor123*" not in html, "ninguna contrasena en claro")
check(f"{total}<small class=\"kpi-unidad\">de {total}</small>" in html,
      "todas las cuentas sembradas cumplen la politica de hash")
check(METODO_HASH in html, "el KPI nombra la politica vigente")
check(html.count("Vigente") >= total, "todas las cuentas sembradas estan vigentes")
check(f'/admin/usuarios/{id_admin}/contrasena">Cambiar la mia' in html,
      "la cuenta propia se cambia desde su ficha")
check("Solo el cliente la cambia" in html, "la del cliente no ofrece acciones")
for id_, quien in ((id_admin, "la propia"), (id_cliente, "la del cliente")):
    check(re.search(rf'value="{id_}" form="form-masivo"[^>]*disabled', html) is not None,
          f"{quien} no se puede seleccionar para el cambio masivo")
for id_, quien in ((id_despachador, "la del gestor"), (id_conductor1, "la del conductor")):
    check(re.search(rf'value="{id_}" form="form-masivo"[^>]*disabled', html) is None,
          f"{quien} si se puede seleccionar")

print("\n== 3. Asignar una contrasena: validaciones ==")
ruta_asignar = f"/admin/usuarios/{id_conductor2}/contrasena"
check(admin.get(ruta_asignar).status_code == 200, "el formulario de asignacion carga")
r = admin.post(ruta_asignar, data={"contrasena": "corta", "confirmacion": "corta",
                                   "exigir_cambio": "y"})
check(b"Minimo 8 caracteres" in r.data, "exige la longitud minima")
r = admin.post(ruta_asignar, data={"contrasena": "Asignada123*", "confirmacion": "Otra12345*",
                                   "exigir_cambio": "y"})
check(b"no coinciden" in r.data, "exige que la confirmacion coincida")
check(usuario("conductor2@sgds.com").verificar_contrasena("Conductor123*"),
      "un formulario invalido no toca la contrasena")
check(admin.get("/admin/usuarios/99999/contrasena").status_code == 404,
      "una cuenta inexistente responde 404")

print("\n== 4. Asignar con cambio exigido cierra sesiones y obliga a cambiarla ==")
abierta = sesion("conductor2@sgds.com", "Conductor123*")
check(opera(abierta, "/conductor/"), "el conductor tiene una sesion abierta")

r = admin.post(ruta_asignar, data={"contrasena": "Asignada123*", "confirmacion": "Asignada123*",
                                   "exigir_cambio": "y"}, follow_redirects=True)
check(b"actualizada" in r.data and b"debera cambiarla" in r.data, "confirma la asignacion")
c2 = usuario("conductor2@sgds.com")
check(c2.verificar_contrasena("Asignada123*"), "la contrasena asignada es valida")
check(not c2.verificar_contrasena("Conductor123*"), "la anterior deja de servir")
check(c2.debe_cambiar_contrasena, "queda pendiente de cambio")
check(c2.contrasena_actualizada_en is not None, "registra la fecha del cambio")

r = abierta.get("/conductor/")
check(r.status_code == 302 and "/auth/login" in destino(r),
      "la sesion abierta con la contrasena anterior queda cerrada")

r = app.test_client().post("/auth/login", data={"correo": "conductor2@sgds.com",
                                                 "contrasena": "Asignada123*"})
check("/auth/perfil" in destino(r), "al iniciar sesion lo lleva directo al perfil")

r = app.test_client().post("/auth/login?next=/conductor/historial",
                           data={"correo": "conductor2@sgds.com", "contrasena": "Asignada123*"})
check("/auth/perfil" in destino(r), "aunque el login traiga un `next`")

c = sesion("conductor2@sgds.com", "Asignada123*")
for ruta in ("/conductor/", "/conductor/historial", "/admin/", "/pedidos/"):
    r = c.get(ruta)
    check(r.status_code == 302 and "/auth/perfil" in destino(r),
          f"con la contrasena asignada {ruta} lo devuelve al perfil")
r = c.get("/auth/perfil")
check(r.status_code == 200 and b"asignada por el administrador" in r.data,
      "el perfil explica por que debe cambiarla")

r = c.post("/auth/perfil", data={"contrasena_actual": "Asignada123*",
                                 "contrasena_nueva": "Asignada123*"}, follow_redirects=True)
check(b"distinta de la actual" in r.data, "no acepta repetir la contrasena asignada")
check(usuario("conductor2@sgds.com").debe_cambiar_contrasena, "y sigue pendiente")

r = c.post("/auth/perfil", data={"contrasena_actual": "Asignada123*",
                                 "contrasena_nueva": "MiPropia2026*"})
check(r.status_code == 302 and "/conductor/" in destino(r),
      "al definir la suya lo lleva a su vista de inicio")
check(opera(c, "/conductor/"), "la sesion con la que la cambio sigue abierta")
c2 = usuario("conductor2@sgds.com")
check(not c2.debe_cambiar_contrasena, "deja de estar pendiente")
check(c2.verificar_contrasena("MiPropia2026*"), "la nueva es la valida")

print("\n== 5. Asignar sin exigir el cambio ==")
r = admin.post(ruta_asignar, data={"contrasena": "Acordada123*", "confirmacion": "Acordada123*"},
               follow_redirects=True)
check(b"actualizada" in r.data and b"debera cambiarla" not in r.data,
      "confirma sin anunciar cambio obligatorio")
check(not usuario("conductor2@sgds.com").debe_cambiar_contrasena, "no queda pendiente")
check(opera(sesion("conductor2@sgds.com", "Acordada123*"), "/conductor/"),
      "el conductor entra directo con la contrasena acordada")

print("\n== 6. Generar una temporal para el gestor logistico ==")
abierta = sesion("despachador@sgds.com", "Despacho123*")
check(opera(abierta, "/pedidos/"), "el gestor tiene una sesion abierta")
r = admin.post(f"/admin/usuarios/{id_despachador}/restablecer",
               data={"volver": "/admin/usuarios/contrasenas?estado=pendientes"})
check(r.status_code == 302 and destino(r).endswith("/admin/usuarios/contrasenas?estado=pendientes"),
      "vuelve a la vista desde la que se lanzo")
html = admin.get("/admin/usuarios/contrasenas?estado=pendientes").data.decode()
temporal = temporal_de(html, "despachador@sgds.com")
check(bool(temporal) and len(temporal) >= 12, "muestra la temporal una vez")
check("despachador@sgds.com" in html and "cliente@sgds.com" not in html,
      "el filtro de pendientes solo lista las pendientes")
check(temporal_de(admin.get("/admin/usuarios/contrasenas").data.decode(), "despachador@sgds.com")
      is None, "y no la vuelve a mostrar")
check(usuario("despachador@sgds.com").debe_cambiar_contrasena, "la temporal queda pendiente de cambio")
check(abierta.get("/pedidos/").status_code == 302, "la sesion abierta del gestor se cierra")

r = admin.post(f"/admin/usuarios/{id_despachador}/restablecer", data={"volver": "//evil.com"})
check(destino(r).endswith("/admin/usuarios/"), "un `volver` hacia otro sitio se ignora")
r = admin.post(f"/admin/usuarios/{id_despachador}/restablecer",
               data={"volver": "https://evil.com/x"})
check("evil.com" not in destino(r), "tampoco con esquema explicito")
admin.get("/admin/usuarios/")  # consume los flashes de las temporales

print("\n== 6b. La contrasena del cliente solo la cambia el cliente ==")
hash_cliente = usuario("cliente@sgds.com").contrasena_hash
abierta = sesion("cliente@sgds.com", "Cliente123*")
r = admin.get(f"/admin/usuarios/{id_cliente}/contrasena", follow_redirects=True)
check(b"Solo el cliente puede cambiar su propia contrasena" in r.data,
      "su ficha no se abre y explica por que")
r = admin.post(f"/admin/usuarios/{id_cliente}/contrasena",
               data={"contrasena": "Tomada123*", "confirmacion": "Tomada123*"})
check("/admin/usuarios/contrasenas" in destino(r), "un POST fabricado a su ficha no asigna nada")
r = admin.post(f"/admin/usuarios/{id_cliente}/restablecer", follow_redirects=True)
check(b"Solo el cliente" in r.data and temporal_de(r.data.decode(), "cliente@sgds.com") is None,
      "no se le puede generar una temporal")
r = admin.post(f"/admin/usuarios/{id_cliente}/alternar-exigencia", follow_redirects=True)
check(b"Solo el cliente" in r.data, "ni exigirle el cambio")
cl = usuario("cliente@sgds.com")
check(cl.contrasena_hash == hash_cliente and not cl.debe_cambiar_contrasena,
      "su credencial queda intacta")
check(opera(abierta, "/portal/"), "y su sesion sigue abierta")
check(b"Restablecer clave" not in admin.get("/admin/usuarios/?rol=CLIENTE").data,
      "el listado de cuentas no ofrece restablecer la de un cliente")
check(b"Restablecer clave" in admin.get("/admin/usuarios/?rol=CONDUCTOR").data,
      "pero si la de un conductor")
html = admin.get(f"/admin/usuarios/{id_cliente}/editar").data.decode()
check("Solo el cliente puede cambiar su contrasena" in html, "su ficha de cuenta lo explica")

r = abierta.post("/auth/perfil", data={"contrasena_actual": "Cliente123*",
                                       "contrasena_nueva": "ClientePropia1*"}, follow_redirects=True)
check(b"Contrasena actualizada" in r.data, "el cliente cambia la suya desde su perfil")
check(opera(sesion("cliente@sgds.com", "ClientePropia1*"), "/portal/"), "y entra con la nueva")

print("\n== 6c. La de otro administrador solo la cambia el ==")
with app.app_context():
    otro = Usuario(nombre="Otro Admin", correo="otro.admin@sgds.com", rol=Rol.ADMIN, activo=True)
    otro.establecer_contrasena("OtroAdmin123*")
    db.session.add(otro)
    db.session.commit()
    id_otro = otro.id
hash_otro = usuario("otro.admin@sgds.com").contrasena_hash
r = admin.get(f"/admin/usuarios/{id_otro}/contrasena", follow_redirects=True)
check(b"Solo cada administrador puede cambiar su propia contrasena" in r.data,
      "su ficha no se abre")
r = admin.post(f"/admin/usuarios/{id_otro}/contrasena",
               data={"contrasena": "Tomada123*", "confirmacion": "Tomada123*"})
check("/admin/usuarios/contrasenas" in destino(r), "un POST fabricado no asigna nada")
r = admin.post(f"/admin/usuarios/{id_otro}/restablecer", follow_redirects=True)
check(b"Solo cada administrador" in r.data, "no se le puede generar una temporal")
r = admin.post(f"/admin/usuarios/{id_otro}/alternar-exigencia", follow_redirects=True)
check(b"Solo cada administrador" in r.data, "ni exigirle el cambio")
o = usuario("otro.admin@sgds.com")
check(o.contrasena_hash == hash_otro and not o.debe_cambiar_contrasena,
      "su credencial queda intacta")
check(b"Solo el la cambia" in admin.get("/admin/usuarios/contrasenas?q=otro.admin").data,
      "el apartado no le ofrece acciones")

print("\n== 7. Exigir el cambio sin tocar la contrasena ==")
abierta = sesion("conductor1@sgds.com", "Conductor123*")
check(opera(abierta, "/conductor/"), "el conductor 1 tiene una sesion abierta")
hash_antes = usuario("conductor1@sgds.com").contrasena_hash

r = admin.post(f"/admin/usuarios/{id_conductor1}/alternar-exigencia", follow_redirects=True)
check(b"debera cambiar su contrasena" in r.data, "confirma la exigencia")
c1 = usuario("conductor1@sgds.com")
check(c1.debe_cambiar_contrasena, "queda pendiente")
check(c1.contrasena_hash == hash_antes, "el hash no cambia")

r = abierta.get("/conductor/")
check(r.status_code == 302 and "/auth/perfil" in destino(r),
      "la sesion abierta queda retenida en el perfil desde su siguiente peticion")
check(abierta.get("/auth/perfil").status_code == 200, "puede ver su perfil")
r = abierta.get("/auth/logout")
check(r.status_code == 302 and "/auth/login" in destino(r), "y puede cerrar sesion")

r = admin.post(f"/admin/usuarios/{id_conductor1}/alternar-exigencia", follow_redirects=True)
check(b"ya no esta obligado" in r.data, "la exigencia se puede retirar")
check(opera(sesion("conductor1@sgds.com", "Conductor123*"), "/conductor/"),
      "y el conductor vuelve a operar con su contrasena de siempre")

print("\n== 8. El administrador cambia la suya, con la actual ==")
ruta_propia = f"/admin/usuarios/{id_admin}/contrasena"
hash_admin = usuario("admin@sgds.com").contrasena_hash
r = admin.get(ruta_propia)
html = r.data.decode()
check(r.status_code == 200 and "Mi contrasena" in html, "su ficha abre en el apartado")
check('name="contrasena_actual"' in html, "pide la contrasena actual")
check('name="exigir_cambio"' not in html and "Generar temporal" not in html,
      "sin las acciones que se aplican a cuentas ajenas")

r = admin.post(ruta_propia, data={"contrasena_actual": "Equivocada1*",
                                  "contrasena": "AdminNueva2026*",
                                  "confirmacion": "AdminNueva2026*"})
check(b"La contrasena actual no es correcta" in r.data, "rechaza una actual equivocada")
r = admin.post(ruta_propia, data={"contrasena_actual": "Admin123*", "contrasena": "Admin123*",
                                  "confirmacion": "Admin123*"})
check(b"distinta de la actual" in r.data, "rechaza repetir la actual")
r = admin.post(ruta_propia, data={"contrasena_actual": "Admin123*",
                                  "contrasena": "AdminNueva2026*", "confirmacion": "Otra2026**"})
check(b"no coinciden" in r.data, "exige que la confirmacion coincida")
r = admin.post(ruta_propia, data={"contrasena_actual": "Admin123*", "contrasena": "corta",
                                  "confirmacion": "corta"})
check(b"Minimo 8 caracteres" in r.data, "exige la longitud minima")
check(usuario("admin@sgds.com").contrasena_hash == hash_admin, "los intentos invalidos no la tocan")

otra_admin = sesion("admin@sgds.com", "Admin123*")
check(opera(otra_admin, "/admin/usuarios/contrasenas"), "tiene otra sesion abierta")
r = admin.post(ruta_propia, data={"contrasena_actual": "Admin123*",
                                  "contrasena": "AdminNueva2026*",
                                  "confirmacion": "AdminNueva2026*"}, follow_redirects=True)
check(b"Su contrasena fue actualizada" in r.data, "confirma el cambio")
a = usuario("admin@sgds.com")
check(a.verificar_contrasena("AdminNueva2026*") and not a.debe_cambiar_contrasena,
      "la nueva queda vigente, sin exigencia de cambio")
check(opera(admin, "/admin/usuarios/contrasenas"), "la sesion que hizo el cambio sigue abierta")
check(otra_admin.get("/admin/usuarios/contrasenas").status_code == 302,
      "la otra sesion queda cerrada")

r = admin.post(f"/admin/usuarios/{id_admin}/restablecer", follow_redirects=True)
check(b"con la actual" in r.data, "no puede generarse una temporal")
r = admin.post(f"/admin/usuarios/{id_admin}/alternar-exigencia", follow_redirects=True)
check(b"con la actual" in r.data, "ni exigirse el cambio")
check(usuario("admin@sgds.com").verificar_contrasena("AdminNueva2026*"),
      "su credencial sigue siendo la que eligio")
check(b"Restablecer clave" not in admin.get("/admin/usuarios/?q=admin@sgds.com").data,
      "el listado de cuentas no le ofrece restablecer la propia")
html = admin.get(f"/admin/usuarios/{id_admin}/editar").data.decode()
check(ruta_propia in html, "su ficha de cuenta remite a su contrasena")

print("\n== 9. Exigir el cambio en bloque ==")
r = admin.post("/admin/usuarios/contrasenas/exigir-cambio", follow_redirects=True)
check(b"Seleccione al menos una cuenta" in r.data, "sin seleccion no hace nada")
for ids, quien in (([id_admin], "la propia"), ([id_cliente, id_otro], "clientes y admins")):
    r = admin.post("/admin/usuarios/contrasenas/exigir-cambio",
                   data={"usuarios": [str(i) for i in ids]}, follow_redirects=True)
    check(b"Seleccione al menos una cuenta de gestor" in r.data,
          f"seleccionar solo {quien} no hace nada")

r = admin.post("/admin/usuarios/contrasenas/exigir-cambio",
               data={"usuarios": [str(id_conductor1), str(id_conductor2), str(id_admin),
                                  str(id_cliente), str(id_otro), "x"]},
               follow_redirects=True)
check(b"2 cuenta(s) deberan cambiar" in r.data, "marca solo los dos conductores seleccionados")
check(b"Se omitieron las cuentas de clientes y administradores" in r.data,
      "avisa que omitio las demas")
check(usuario("conductor1@sgds.com").debe_cambiar_contrasena, "el conductor 1 queda pendiente")
check(usuario("conductor2@sgds.com").debe_cambiar_contrasena, "el conductor 2 queda pendiente")
check(not usuario("admin@sgds.com").debe_cambiar_contrasena, "la cuenta propia se excluye")
check(not usuario("cliente@sgds.com").debe_cambiar_contrasena, "la del cliente se excluye")
check(not usuario("otro.admin@sgds.com").debe_cambiar_contrasena, "la del otro admin se excluye")

r = admin.post("/admin/usuarios/contrasenas/exigir-cambio",
               data={"usuarios": [str(id_despachador)]}, follow_redirects=True)
check(b"0 cuenta(s)" in r.data, "una cuenta ya pendiente no se cuenta dos veces")

html = admin.get("/admin/usuarios/contrasenas").data.decode()
check(re.search(r"Pendientes de cambio</span>\s*<span class=\"kpi-valor\">3<", html) is not None,
      "el KPI suma las tres cuentas pendientes")
admin.post(f"/admin/usuarios/{id_conductor2}/alternar-exigencia")  # la usa la seccion 11

print("\n== 10. Alta de cuentas: temporal obligatoria, escrita a eleccion ==")
def alta(correo, **extra):
    datos = {"nombre": "Cuenta Alta", "correo": correo, "rol": Rol.CONDUCTOR,
             "cliente_id": "0", "activo": "y", "contrasena": ""}
    datos.update(extra)
    return admin.post("/admin/usuarios/nuevo", data=datos, follow_redirects=True)

alta("generada@sgds.com", exigir_cambio="")
check(usuario("generada@sgds.com").debe_cambiar_contrasena,
      "una contrasena generada siempre es temporal, aunque se desmarque la opcion")
alta("escrita.exigida@sgds.com", contrasena="Escrita123*", exigir_cambio="y")
check(usuario("escrita.exigida@sgds.com").debe_cambiar_contrasena,
      "una escrita con la opcion marcada queda pendiente")
alta("escrita.libre@sgds.com", contrasena="Escrita123*")
check(not usuario("escrita.libre@sgds.com").debe_cambiar_contrasena,
      "una escrita sin la opcion queda vigente")
check(opera(sesion("escrita.libre@sgds.com", "Escrita123*"), "/conductor/"),
      "y esa cuenta opera desde el primer inicio de sesion")
alta("gestor.libre@sgds.com", rol=Rol.DESPACHADOR, contrasena="Escrita123*")
check(not usuario("gestor.libre@sgds.com").debe_cambiar_contrasena,
      "lo mismo para un gestor logistico")

alta("admin.nuevo@sgds.com", rol=Rol.ADMIN, contrasena="Escrita123*")
check(usuario("admin.nuevo@sgds.com").debe_cambiar_contrasena,
      "la inicial de un administrador siempre es temporal, aunque se desmarque la opcion")
with app.app_context():
    id_libre_cliente = (db.session.query(Cliente.id)
                        .filter(Cliente.usuario_id.is_(None), Cliente.activo.is_(True)).first()[0])
alta("portal.alta@sgds.com", rol=Rol.CLIENTE, cliente_id=str(id_libre_cliente),
     contrasena="Escrita123*")
check(usuario("portal.alta@sgds.com").debe_cambiar_contrasena,
      "la inicial de un cliente siempre es temporal: solo el conocera la definitiva")
r = sesion("portal.alta@sgds.com", "Escrita123*").get("/portal/")
check(r.status_code == 302 and "/auth/perfil" in destino(r),
      "el cliente nuevo debe definir la suya antes de ver su portal")

id_libre = usuario("escrita.libre@sgds.com").id
html = admin.get(f"/admin/usuarios/{id_libre}/editar").data.decode()
check('name="contrasena"' not in html, "la edicion ya no tiene campo de contrasena")
check(f"/admin/usuarios/{id_libre}/contrasena" in html, "remite al apartado de la cuenta")
hash_libre = usuario("escrita.libre@sgds.com").contrasena_hash
admin.post(f"/admin/usuarios/{id_libre}/editar",
           data={"nombre": "Cuenta Editada", "correo": "escrita.libre@sgds.com",
                 "rol": Rol.CONDUCTOR, "cliente_id": "0", "activo": "y",
                 "contrasena": "Inyectada123*"})
e = usuario("escrita.libre@sgds.com")
check(e.nombre == "Cuenta Editada", "la edicion guarda los datos")
check(e.contrasena_hash == hash_libre, "un POST de edicion con contrasena no la cambia")

print("\n== 11. Cambiar la propia contrasena cierra las demas sesiones ==")
otra = sesion("conductor2@sgds.com", "Acordada123*")
esta = sesion("conductor2@sgds.com", "Acordada123*")
check(opera(otra, "/conductor/") and opera(esta, "/conductor/"), "dos sesiones abiertas")
r = esta.post("/auth/perfil", data={"contrasena_actual": "Acordada123*",
                                    "contrasena_nueva": "OtraMas2026*"}, follow_redirects=True)
check(b"Contrasena actualizada" in r.data, "el cambio desde el perfil se confirma")
check(opera(esta, "/conductor/"), "la sesion que hizo el cambio sigue abierta")
check(otra.get("/conductor/").status_code == 302, "la otra sesion queda cerrada")

print("\n== 12. Hash fuera de la politica ==")
with app.app_context():
    vieja = Usuario(nombre="Cuenta Vieja", correo="vieja@sgds.com", rol=Rol.CONDUCTOR, activo=True)
    vieja.establecer_contrasena("Vieja12345*")
    from werkzeug.security import generate_password_hash
    vieja.contrasena_hash = generate_password_hash("Vieja12345*", method="pbkdf2:sha256:1000")
    db.session.add(vieja)
    db.session.commit()
    total = db.session.query(Usuario).count()
    id_vieja = vieja.id

html = admin.get("/admin/usuarios/contrasenas?q=vieja").data.decode()
check("Revisar" in html, "la cuenta con el hash antiguo se marca para revisar")
html = admin.get("/admin/usuarios/contrasenas").data.decode()
check(f"{total - 1}<small class=\"kpi-unidad\">de {total}</small>" in html,
      "el KPI la descuenta de las protegidas")
admin.post(f"/admin/usuarios/{id_vieja}/contrasena",
           data={"contrasena": "Renovada123*", "confirmacion": "Renovada123*", "exigir_cambio": "y"})
check(usuario("vieja@sgds.com").hash_vigente, "asignarle una contrasena la lleva a la politica vigente")

print("\n== 13. Filtros del apartado ==")
for consulta, descripcion in (
    ("?rol=CONDUCTOR", "filtra por rol"),
    ("?estado=pendientes", "filtra las pendientes"),
    ("?estado=vigentes", "filtra las vigentes"),
    ("?q=conductor", "busca por texto"),
    ("?rol=NO_EXISTE&estado=otro", "un filtro invalido no rompe la vista"),
):
    check(admin.get("/admin/usuarios/contrasenas" + consulta).status_code == 200, descripcion)
html = admin.get("/admin/usuarios/contrasenas?estado=vigentes").data.decode()
check("despachador@sgds.com" not in html and "admin@sgds.com" in html,
      "las vigentes excluyen a las pendientes")

print("\n== 14. Migracion de las columnas de contrasenas ==")
from migraciones.m008_gestion_contrasenas import COLUMNAS, aplicar
with app.app_context():
    check(aplicar(verboso=False) == [], "sobre una base al dia no agrega nada")
    with db.engine.begin() as conexion:
        for nombre in COLUMNAS:
            conexion.execute(text(f"ALTER TABLE usuarios DROP COLUMN {nombre}"))
    check(not set(COLUMNAS) & {c["name"] for c in inspect(db.engine).get_columns("usuarios")},
          "la tabla queda como antes del cambio")
    check(aplicar(verboso=False) == list(COLUMNAS), "agrega las tres columnas")
    check(aplicar(verboso=False) == [], "volver a ejecutarla no cambia nada")
    db.session.expire_all()
    migrado = db.session.get(Usuario, id_cliente)
    check(migrado.contrasena_actualizada_en is None, "la fecha de las cuentas existentes queda vacia")
    check(migrado.debe_cambiar_contrasena is False, "sin exigencia de cambio")
    check(migrado.version_credencial == 0, "con la version de credencial en cero")

# La version de credencial vuelve a cero: las sesiones previas caducan una vez.
check(admin.get("/admin/usuarios/contrasenas").status_code == 302,
      "las sesiones abiertas antes de la migracion piden iniciar sesion de nuevo")
admin = sesion("admin@sgds.com", "AdminNueva2026*")
check(opera(admin, "/admin/usuarios/contrasenas"), "tras iniciar sesion el apartado carga")
check(b"Sin registro" in admin.get("/admin/usuarios/contrasenas").data,
      "las cuentas sin fecha se muestran sin registro")


print("\n" + "=" * 55)
print("RESULTADO: " + ("TODAS LAS PRUEBAS PASARON" if not fallos else f"{len(fallos)} FALLAS"))
for f in fallos: print("   - " + f)
sys.exit(1 if fallos else 0)
