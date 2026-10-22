"""Administracion de cuentas de acceso y directorio de clientes (RF1).

Cubre el alta de usuarios desde la interfaz —incluida la habilitacion del portal
de un cliente, que antes exigia entrar a la base— y las salvaguardas que impiden
que un administrador se deje a si mismo sin acceso.
"""

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from run import app
from app.extensions import db
from app.models import Cliente, Rol, Usuario

fallos = []
def check(cond, msg):
    print(("  OK   " if cond else "  FALLA") + f" {msg}")
    if not cond: fallos.append(msg)

app.config["WTF_CSRF_ENABLED"] = False


def sesion(correo, clave):
    c = app.test_client()
    c.post("/auth/login", data={"correo": correo, "contrasena": clave})
    return c


def temporal_de(html, correo):
    """Extrae la contrasena temporal que el sistema muestra una sola vez."""
    m = re.search(r"Contrasena temporal de " + re.escape(correo) + r": (\S+)", html)
    return m.group(1) if m else None


def definir_propia(cliente, actual, nueva):
    """El usuario reemplaza la contrasena temporal desde su perfil."""
    return cliente.post("/auth/perfil", data={"contrasena_actual": actual,
                                             "contrasena_nueva": nueva})


def datos_usuario(**extra):
    base = {"nombre": "Persona Prueba", "correo": "persona@sgds.com",
            "rol": Rol.CONDUCTOR, "cliente_id": "0", "activo": "y", "contrasena": ""}
    base.update(extra)
    return base


admin = sesion("admin@sgds.com", "Admin123*")

print("\n== 1. Solo el administrador gestiona cuentas ==")
check(admin.get("/admin/usuarios/").status_code == 200, "el admin entra al listado de usuarios")
for correo, clave, rol in (
    ("despachador@sgds.com", "Despacho123*", "despachador"),
    ("conductor1@sgds.com", "Conductor123*", "conductor"),
    ("cliente@sgds.com", "Cliente123*", "cliente"),
):
    c = sesion(correo, clave)
    check(c.get("/admin/usuarios/").status_code == 403, f"el {rol} no accede al listado de usuarios")
    check(c.get("/admin/usuarios/nuevo").status_code == 403, f"el {rol} no puede crear cuentas")
check(app.test_client().get("/admin/usuarios/").status_code == 302, "un anonimo es redirigido")

print("\n== 2. El directorio de clientes es de la operacion ==")
check(sesion("despachador@sgds.com", "Despacho123*").get("/admin/clientes/").status_code == 200,
      "el despachador consulta el directorio de clientes")
check(sesion("conductor1@sgds.com", "Conductor123*").get("/admin/clientes/").status_code == 403,
      "el conductor no accede al directorio")
check(sesion("cliente@sgds.com", "Cliente123*").get("/admin/clientes/").status_code == 403,
      "el cliente no accede al directorio interno")

print("\n== 3. Alta de un conductor con contrasena generada ==")
r = admin.post("/admin/usuarios/nuevo",
               data=datos_usuario(nombre="Nuevo Conductor", correo="nuevo.conductor@sgds.com"),
               follow_redirects=True)
html = r.data.decode()
check(r.status_code == 200 and "creada" in html, "la cuenta se crea")
clave_generada = temporal_de(html, "nuevo.conductor@sgds.com")
check(bool(clave_generada), "el sistema emite una contrasena temporal")
check(clave_generada is not None and len(clave_generada) >= 12, "la temporal tiene longitud suficiente")

with app.app_context():
    creado = db.session.query(Usuario).filter_by(correo="nuevo.conductor@sgds.com").one()
    check(creado.rol == Rol.CONDUCTOR, "queda con el rol indicado")
    check(creado.contrasena_hash != clave_generada, "la contrasena no se guarda en claro (RNF5)")
    check(creado.verificar_contrasena(clave_generada), "el hash valida la temporal emitida")

nuevo = sesion("nuevo.conductor@sgds.com", clave_generada)
r = nuevo.get("/conductor/")
check(r.status_code == 302 and "/auth/perfil" in r.headers.get("Location", ""),
      "con la temporal, el conductor nuevo solo llega a su perfil")
definir_propia(nuevo, clave_generada, "ConductorNuevo123*")
r = nuevo.get("/conductor/")
check(r.status_code == 200 and b"Mi ruta" in r.data,
      "tras definir su contrasena entra a su vista")

print("\n== 4. Validaciones del alta ==")
r = admin.post("/admin/usuarios/nuevo",
               data=datos_usuario(correo="admin@sgds.com"), follow_redirects=True)
check(b"Ya existe una cuenta con este correo" in r.data, "rechaza un correo repetido")

r = admin.post("/admin/usuarios/nuevo",
               data=datos_usuario(correo="otra@sgds.com", contrasena="corta"),
               follow_redirects=True)
check(b"Minimo 8 caracteres" in r.data, "exige la longitud minima de contrasena")

r = admin.post("/admin/usuarios/nuevo",
               data=datos_usuario(correo="sincliente@sgds.com", rol=Rol.CLIENTE, cliente_id="0"),
               follow_redirects=True)
check(b"debe apuntar al cliente" in r.data, "una cuenta de cliente exige el cliente asociado")
with app.app_context():
    check(db.session.query(Usuario).filter_by(correo="sincliente@sgds.com").first() is None,
          "la cuenta invalida no se creo")

print("\n== 5. Habilitar el portal de un cliente ==")
with app.app_context():
    libre = db.session.query(Cliente).filter(Cliente.usuario_id.is_(None)).first()
    id_libre, nombre_libre = libre.id, libre.nombre
    tomado = db.session.query(Cliente).filter(Cliente.usuario_id.isnot(None)).first()
    id_tomado, correo_tomado = tomado.id, tomado.usuario.correo

r = admin.get(f"/admin/usuarios/nuevo?cliente_id={id_libre}")
html = r.data.decode()
check(f'value="{nombre_libre}"' in html, "el formulario llega prellenado desde el directorio")
check('selected value="CLIENTE"' in html, "y con el rol Cliente preseleccionado")

r = admin.post("/admin/usuarios/nuevo",
               data=datos_usuario(nombre=nombre_libre, correo="portal.nuevo@empresa.com",
                                  rol=Rol.CLIENTE, cliente_id=str(id_libre)),
               follow_redirects=True)
html = r.data.decode()
check(f"Portal habilitado para {nombre_libre}" in html, "confirma la habilitacion del portal")
clave_portal = temporal_de(html, "portal.nuevo@empresa.com")

with app.app_context():
    vinculado = db.session.get(Cliente, id_libre)
    check(vinculado.usuario is not None, "clientes.usuario_id quedo escrito")
    check(vinculado.usuario.correo == "portal.nuevo@empresa.com", "apunta a la cuenta correcta")

portal = sesion("portal.nuevo@empresa.com", clave_portal)
definir_propia(portal, clave_portal, "PortalNuevo123*")
clave_portal = "PortalNuevo123*"
r = portal.get("/", follow_redirects=True)
check(b"Mis pedidos" in r.data, "el cliente nuevo aterriza en su portal")
check(nombre_libre in r.data.decode(), "y ve su propia razon social")

print("\n== 6. Un cliente no puede tener dos cuentas ==")
# La relacion es 1 a 0..1 y la columna es unica: el cliente ya tomado no debe
# aparecer como opcion ni aceptarse en un POST fabricado a mano.
html = admin.get("/admin/usuarios/nuevo").data.decode()
check(f'value="{id_tomado}"' not in html, "el cliente con cuenta no se ofrece en el select")

r = admin.post("/admin/usuarios/nuevo",
               data=datos_usuario(correo="segunda.cuenta@empresa.com",
                                  rol=Rol.CLIENTE, cliente_id=str(id_tomado)),
               follow_redirects=True)
check(b"no esta disponible" in r.data, "un POST fabricado con ese cliente se rechaza")
with app.app_context():
    check(db.session.query(Usuario).filter_by(correo="segunda.cuenta@empresa.com").first() is None,
          "no se creo la cuenta duplicada")
    todavia = db.session.get(Cliente, id_tomado)
    check(todavia.usuario.correo == correo_tomado, "el vinculo original quedo intacto")

print("\n== 7. Cambiar el rol desvincula el portal ==")
with app.app_context():
    cuenta = db.session.query(Usuario).filter_by(correo="portal.nuevo@empresa.com").one()
    id_cuenta = cuenta.id

r = admin.post(f"/admin/usuarios/{id_cuenta}/editar",
               data=datos_usuario(nombre="Ahora Despachador",
                                  correo="portal.nuevo@empresa.com",
                                  rol=Rol.DESPACHADOR, cliente_id="0"),
               follow_redirects=True)
check(b"actualizada" in r.data, "el cambio de rol se guarda")
with app.app_context():
    check(db.session.get(Cliente, id_libre).usuario_id is None,
          "el cliente queda sin cuenta al dejar la cuenta de ser CLIENTE")
    check(db.session.get(Usuario, id_cuenta).rol == Rol.DESPACHADOR, "la cuenta tiene el rol nuevo")

r = sesion("portal.nuevo@empresa.com", clave_portal).get("/portal/")
check(r.status_code == 403, "esa cuenta ya no entra al portal del cliente")

print("\n== 8. Un administrador no puede dejarse sin acceso ==")
with app.app_context():
    yo = db.session.query(Usuario).filter_by(correo="admin@sgds.com").one()
    id_admin = yo.id

r = admin.post(f"/admin/usuarios/{id_admin}/editar",
               data=datos_usuario(nombre="Laura Gomez", correo="admin@sgds.com",
                                  rol=Rol.CONDUCTOR),
               follow_redirects=True)
check(b"No puede cambiar su propio rol" in r.data, "no puede cambiarse el rol")

r = admin.post(f"/admin/usuarios/{id_admin}/editar",
               data={"nombre": "Laura Gomez", "correo": "admin@sgds.com",
                     "rol": Rol.ADMIN, "cliente_id": "0", "contrasena": ""},
               follow_redirects=True)
check(b"No puede desactivar su propia cuenta" in r.data, "no puede desactivarse editando")

r = admin.post(f"/admin/usuarios/{id_admin}/alternar-estado", follow_redirects=True)
check(b"No puede desactivar su propia cuenta" in r.data, "no puede desactivarse con la accion rapida")

with app.app_context():
    db.session.expire_all()
    yo = db.session.query(Usuario).filter_by(correo="admin@sgds.com").one()
    check(yo.activo and yo.rol == Rol.ADMIN, "la cuenta del admin quedo intacta")

check(admin.get("/admin/usuarios/").status_code == 200, "el admin conserva el acceso")

print("\n== 9. Debe quedar un administrador activo ==")
with app.app_context():
    otro = Usuario(nombre="Segundo Admin", correo="admin2@sgds.com", rol=Rol.ADMIN, activo=True)
    otro.establecer_contrasena("Admin2Clave*")
    db.session.add(otro)
    db.session.commit()
    id_otro = otro.id

# Con dos administradores activos, desactivar a uno si esta permitido.
r = admin.post(f"/admin/usuarios/{id_otro}/alternar-estado", follow_redirects=True)
check(b"desactivada" in r.data, "con dos admin activos si se puede desactivar a uno")
with app.app_context():
    check(db.session.get(Usuario, id_otro).activo is False, "quedo inactivo")

r = sesion("admin2@sgds.com", "Admin2Clave*").get("/admin/usuarios/")
check(r.status_code in (302, 401), "una cuenta desactivada no inicia sesion")

print("\n== 10. Restablecer contrasena ==")
with app.app_context():
    objetivo = db.session.query(Usuario).filter_by(correo="conductor2@sgds.com").one()
    id_objetivo, hash_anterior = objetivo.id, objetivo.contrasena_hash

r = admin.post(f"/admin/usuarios/{id_objetivo}/restablecer", follow_redirects=True)
nueva = temporal_de(r.data.decode(), "conductor2@sgds.com")
check(bool(nueva), "emite una contrasena temporal nueva")

with app.app_context():
    db.session.expire_all()
    objetivo = db.session.get(Usuario, id_objetivo)
    check(objetivo.contrasena_hash != hash_anterior, "el hash almacenado cambio")
    check(objetivo.verificar_contrasena(nueva), "la nueva temporal es valida")
    check(not objetivo.verificar_contrasena("Conductor123*"), "la anterior deja de servir")

conductor2 = sesion("conductor2@sgds.com", nueva)
r = conductor2.get("/conductor/")
check(r.status_code == 302, "con la temporal nueva solo puede cambiarla")
definir_propia(conductor2, nueva, "Conductor2Nueva*")
r = conductor2.get("/conductor/")
check(r.status_code == 200 and b"Mi ruta" in r.data, "tras cambiarla vuelve a operar")

print("\n== 11. Alta y correccion de clientes ==")
despachador = sesion("despachador@sgds.com", "Despacho123*")
r = despachador.post("/admin/clientes/nuevo", data={
    "nombre": "Distribuidora Sur", "documento": "902222222-1",
    "telefono": "3001112233", "correo": "compras@sur.com", "activo": "y",
}, follow_redirects=True)
check(b"registrado" in r.data, "el despachador registra un cliente nuevo")

with app.app_context():
    sur = db.session.query(Cliente).filter_by(documento="902222222-1").one()
    id_sur = sur.id
    check(sur.nombre_normalizado == "distribuidora sur", "la clave de deduplicacion se calcula al crear")

# Un homonimo sin NIT que lo distinga es casi siempre un duplicado por error.
r = despachador.post("/admin/clientes/nuevo", data={
    "nombre": "DISTRIBUIDORA  SUR.", "documento": "", "activo": "y",
}, follow_redirects=True)
check(b"ya existe y no se distingue" in r.data, "bloquea un duplicado por escritura distinta")

r = despachador.post(f"/admin/clientes/{id_sur}/editar", data={
    "nombre": "Distribuidora Sur S.A.S.", "documento": "902222222-1",
    "telefono": "3001112233", "correo": "compras@sur.com", "activo": "y",
}, follow_redirects=True)
check(b"actualizado" in r.data, "corrige la razon social")
with app.app_context():
    sur = db.session.get(Cliente, id_sur)
    check(sur.nombre_normalizado == "distribuidora sur s a s",
          "renombrar resincroniza la clave de deduplicacion")

print("\n== 12. Agregar una sede desde el directorio ==")
with app.app_context():
    antes = len(db.session.get(Cliente, id_sur).direcciones)

r = despachador.post(f"/admin/clientes/{id_sur}", data={
    "etiqueta": "Bodega Sur", "direccion": "Av. Villavicencio #45-10",
    "ciudad": "Bogota", "ventana_inicio": "08:00", "ventana_fin": "12:00",
}, follow_redirects=True)
check(b"agregada" in r.data, "la sede se registra")
with app.app_context():
    check(len(db.session.get(Cliente, id_sur).direcciones) == antes + 1, "el cliente suma una sede")

# La misma direccion escrita distinto no debe duplicar la sede.
r = despachador.post(f"/admin/clientes/{id_sur}", data={
    "etiqueta": "Otra", "direccion": "av. villavicencio #45-10", "ciudad": "bogota",
}, follow_redirects=True)
check(b"ya tiene registrada esa direccion" in r.data, "rechaza la sede duplicada")
with app.app_context():
    check(len(db.session.get(Cliente, id_sur).direcciones) == antes + 1, "no se agrego una segunda vez")

print("\n== 13. Las cuentas no se eliminan, se desactivan ==")
with app.app_context():
    conductor = db.session.query(Usuario).filter_by(correo="conductor1@sgds.com").one()
    id_conductor = conductor.id
    rutas_previas = conductor.rutas.count()

r = admin.post(f"/admin/usuarios/{id_conductor}/alternar-estado", follow_redirects=True)
check(b"desactivada" in r.data, "la cuenta se desactiva")
with app.app_context():
    db.session.expire_all()
    conductor = db.session.get(Usuario, id_conductor)
    check(conductor is not None, "el registro sigue existiendo")
    check(conductor.activo is False, "queda inactivo")
    check(conductor.rutas.count() == rutas_previas,
          "su historico de rutas se conserva intacto (trazabilidad del RF6)")

r = app.test_client().post("/auth/login",
                           data={"correo": "conductor1@sgds.com", "contrasena": "Conductor123*"},
                           follow_redirects=True)
check(b"inactiva" in r.data, "el login avisa que la cuenta esta inactiva")

r = admin.post(f"/admin/usuarios/{id_conductor}/alternar-estado", follow_redirects=True)
check(b"activada" in r.data, "se puede reactivar")
r = sesion("conductor1@sgds.com", "Conductor123*").get("/conductor/", follow_redirects=True)
check(b"Mi ruta" in r.data, "y vuelve a operar con su clave original")

print("\n== 13b. Una sesion abierta pierde acceso al desactivar la cuenta ==")
with app.app_context():
    id_conductor1 = db.session.query(Usuario).filter_by(correo="conductor1@sgds.com").one().id

sesion_abierta = sesion("conductor1@sgds.com", "Conductor123*")
r = sesion_abierta.get("/conductor/", follow_redirects=True)
check(b"Mi ruta" in r.data, "la sesion arranca con acceso normal")

r = admin.post(f"/admin/usuarios/{id_conductor1}/alternar-estado", follow_redirects=True)
check(b"desactivada" in r.data, "el admin desactiva la cuenta desde otra sesion")

r = sesion_abierta.get("/conductor/", follow_redirects=False)
check(r.status_code == 302, "la sesion ya abierta pierde el acceso sin volver a iniciar sesion")

r = admin.post(f"/admin/usuarios/{id_conductor1}/alternar-estado", follow_redirects=True)
check(b"activada" in r.data, "se reactiva la cuenta (limpieza)")
r = sesion_abierta.get("/conductor/", follow_redirects=True)
check(b"Mi ruta" in r.data, "al reactivarla la misma sesion recupera el acceso")

print("\n== 14. Filtros del listado ==")
for consulta, descripcion in (
    ("?rol=CONDUCTOR", "filtra por rol"),
    ("?estado=activos", "filtra las cuentas activas"),
    ("?estado=inactivos", "filtra las cuentas inactivas"),
    ("?q=admin", "busca por texto"),
    ("?rol=NO_EXISTE", "un rol invalido no rompe la vista"),
):
    check(admin.get("/admin/usuarios/" + consulta).status_code == 200, descripcion)

for consulta, descripcion in (
    ("?portal=si", "filtra los clientes con portal"),
    ("?portal=no", "filtra los clientes sin portal"),
    ("?q=Portal", "busca clientes por texto"),
):
    check(admin.get("/admin/clientes/" + consulta).status_code == 200, descripcion)

print("\n== 15. Correo con lista blanca en cuentas y clientes (RNF5) ==")
from app.controllers.seguridad import MENSAJE_CORREO

# La misma regla que el login: una cuenta creada con un correo que el login
# rechaza no podria entrar nunca.
for correo, descripcion in (
    ("josé@empresa.com", "tildes"),
    ("ana maria@empresa.com", "espacios"),
    ("compras'--@empresa.com", "comillas"),
    ("<b>x</b>@empresa.com", "etiquetas HTML"),
):
    r = admin.post("/admin/usuarios/nuevo", data=datos_usuario(correo=correo), follow_redirects=True)
    check(MENSAJE_CORREO in r.data.decode(), f"el alta de cuenta rechaza {descripcion}")
    with app.app_context():
        check(db.session.query(Usuario).filter_by(correo=correo.lower()).first() is None,
              f"   y no crea la cuenta ({descripcion})")

r = admin.post("/admin/usuarios/nuevo",
               data=datos_usuario(nombre="Conductor Norte", correo="  Conductor.Norte+2@Empresa.com.co "),
               follow_redirects=True)
temporal_norte = temporal_de(r.data.decode(), "conductor.norte+2@empresa.com.co")
check(bool(temporal_norte), "acepta un correo valido con . + y subdominio, sin los espacios")
r = app.test_client().post("/auth/login", data={"correo": "conductor.norte+2@empresa.com.co",
                                                "contrasena": temporal_norte})
check(r.status_code == 302 and "/auth/perfil" in r.headers.get("Location", ""),
      "y con ese mismo correo se puede iniciar sesion")

with app.app_context():
    norte = db.session.query(Usuario).filter_by(correo="conductor.norte+2@empresa.com.co").one()
    id_norte = norte.id
r = admin.post(f"/admin/usuarios/{id_norte}/editar",
               data=datos_usuario(nombre="Conductor Norte", correo="conductor norte@empresa.com"),
               follow_redirects=True)
check(MENSAJE_CORREO in r.data.decode(), "la edicion de cuenta tambien aplica la regla")
with app.app_context():
    check(db.session.get(Usuario, id_norte).correo == "conductor.norte+2@empresa.com.co",
          "y el correo anterior queda intacto")
check(b"data-correo" in admin.get(f"/admin/usuarios/{id_norte}/editar").data,
      "el formulario de cuenta lleva el filtro del navegador")

despachador = sesion("despachador@sgds.com", "Despacho123*")
r = despachador.post("/admin/clientes/nuevo", data={
    "nombre": "Mayorista Oriente", "correo": "compras<1>@oriente.com", "activo": "y",
}, follow_redirects=True)
check(MENSAJE_CORREO in r.data.decode(), "el correo de un cliente tambien se valida")
with app.app_context():
    check(db.session.query(Cliente).filter_by(nombre="Mayorista Oriente").first() is None,
          "y el cliente no se registra")
r = despachador.post("/admin/clientes/nuevo", data={
    "nombre": "Mayorista Oriente", "correo": "compras_oriente-2@oriente.com.co", "activo": "y",
}, follow_redirects=True)
check(b"registrado" in r.data, "acepta un correo de cliente valido")
r = despachador.post("/admin/clientes/nuevo", data={
    "nombre": "Mayorista Occidente", "correo": "", "activo": "y",
}, follow_redirects=True)
check(b"registrado" in r.data, "el correo del cliente sigue siendo opcional")
check(b"data-correo" in despachador.get("/admin/clientes/nuevo").data,
      "el formulario de cliente lleva el filtro del navegador")

print("\n" + "=" * 55)
print("RESULTADO: " + ("TODAS LAS PRUEBAS PASARON" if not fallos else f"{len(fallos)} FALLAS"))
for f in fallos:
    print("   - " + f)
sys.exit(1 if fallos else 0)
