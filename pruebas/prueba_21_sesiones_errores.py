"""Sesion unica, inactividad, politica de contrasenas, correo propio y errores (RNF5).

Cubre los puntos de la evaluacion de seguridad que no tenian prueba propia:
bloqueo al quinto intento, contrasena con la secuencia del 1 al 15, sesion
simultanea en dos navegadores, tiempo de inactividad configurable (reducirlo y
aumentarlo), cerrar sesion en una pestana y navegar en otra, cambio de correo
desde el perfil y registro de errores, con la migracion m010.
"""

import logging
import pathlib
import re
import sys
import tempfile
from datetime import timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _preparar  # noqa: E402  Primero: base de pruebas, sin Make, Retell ni red externa.
_preparar.reiniciar_base()

# El archivo de errores de esta suite va a una carpeta temporal y no al
# instance/logs del proyecto: `errores.configurar` no agrega otro manejador.
ARCHIVO_ERRORES = pathlib.Path(tempfile.mkdtemp(prefix="sgds_errores_")) / "errores.log"
logging.getLogger("rutalytics.errores").addHandler(logging.FileHandler(ARCHIVO_ERRORES, encoding="utf-8"))
logging.getLogger("rutalytics.errores").setLevel(logging.ERROR)

from sqlalchemy import inspect, text

from run import app
from app.controllers.errores import FalloForzado
from app.extensions import db
from app.models import Auditoria, ErrorSistema, IntentoAcceso, Usuario
from app.services.usuarios import error_politica, generar_contrasena
from app.tiempo import ahora

fallos = []
def check(cond, msg):
    print(("  OK   " if cond else "  FALLA") + f" {msg}")
    if not cond: fallos.append(msg)

app.config["WTF_CSRF_ENABLED"] = False


def sesion(correo, clave, recordar=False):
    c = app.test_client()
    datos = {"correo": correo, "contrasena": clave}
    if recordar:
        datos["recordar"] = "y"
    c.post("/auth/login", data=datos)
    return c


def opera(cliente, ruta):
    return cliente.get(ruta).status_code == 200


def cerrada(cliente, ruta):
    r = cliente.get(ruta)
    return r.status_code == 302 and "/auth/login" in r.headers.get("Location", "")


def alertas(r):
    return " | ".join(re.findall(r'<div class="alerta alerta-\w+">(.*?)</div>', r.data.decode()))


def envejecer(correo, segundos):
    """Simula que la sesion de la cuenta lleva `segundos` sin actividad."""
    with app.app_context():
        u = db.session.query(Usuario).filter_by(correo=correo).one()
        u.sesion_actividad_en = ahora() - timedelta(seconds=segundos)
        db.session.commit()


def usuario(correo):
    with app.app_context():
        u = db.session.query(Usuario).filter_by(correo=correo).one()
        db.session.expunge(u)
        return u


print("\n== 1. Bloqueo al quinto intento fallido ==")
check(app.config["LOGIN_MAX_INTENTOS"] == 5, "el limite configurado es de 5 intentos")
c = app.test_client()
mensajes = [alertas(c.post("/auth/login", data={"correo": "conductor1@sgds.com",
                                                "contrasena": "Equivocada9*"})) for _ in range(5)]
check("Intento fallido 4 de 5" in mensajes[3], "el cuarto fallo aun no bloquea")
check("se superaron los 5 intentos" in mensajes[4], "el quinto bloquea")
r = c.post("/auth/login", data={"correo": "conductor1@sgds.com", "contrasena": "Conductor123*"})
check(r.status_code == 200 and "se superaron" in alertas(r), "ni la correcta entra")
with app.app_context():
    db.session.query(IntentoAcceso).delete()
    db.session.commit()

print("\n== 2. Politica de contrasenas ==")
check(error_politica("123456789101112131415") is not None, "la secuencia del 1 al 15 no cumple")
for mala, falta in (("abcdefgh", "una mayúscula"), ("ABCDEFG1*", "una minúscula"),
                    ("Abcdefgh*", "un número"), ("Abcdefg1", "un símbolo"), ("Ab1*", "mínimo 8")):
    check(falta in (error_politica(mala) or ""), f"'{mala}': le falta {falta}")
check(error_politica("Valida123*") is None, "una contrasena con las cuatro clases cumple")
check(all(error_politica(generar_contrasena()) is None for _ in range(300)),
      "las contrasenas generadas cumplen la politica")

admin = sesion("admin@sgds.com", "Admin123*")
r = admin.post("/admin/usuarios/nuevo", data={
    "nombre": "Secuencia", "correo": "secuencia@sgds.com", "rol": "CONDUCTOR", "cliente_id": "0",
    "activo": "y", "contrasena": "123456789101112131415"}, follow_redirects=True)
check("al menos una mayúscula, una minúscula y un símbolo" in r.data.decode(),
      "crear un usuario con la secuencia del 1 al 15 se rechaza")
with app.app_context():
    check(db.session.query(Usuario).filter_by(correo="secuencia@sgds.com").first() is None,
          "y la cuenta no se crea")
html = admin.get("/admin/usuarios/nuevo").data.decode()
check("con al menos una mayúscula, una minúscula, un número y un símbolo" in html,
      "el formulario de alta explica la politica")
id_c2 = usuario("conductor2@sgds.com").id
r = admin.post(f"/admin/usuarios/{id_c2}/contrasena",
               data={"contrasena": "x", "confirmacion": "x", "exigir_cambio": "y"})
check("mínimo 8 caracteres" in r.data.decode(), "el admin no asigna una contrasena de un caracter")
check(usuario("conductor2@sgds.com").verificar_contrasena("Conductor123*"), "y la anterior sigue")

print("\n== 3. Sesion simultanea en dos navegadores ==")
navegador_a = sesion("despachador@sgds.com", "Despacho123*")
check(opera(navegador_a, "/pedidos/"), "el navegador A opera")
navegador_b = sesion("despachador@sgds.com", "Despacho123*")
check(opera(navegador_b, "/pedidos/"), "el navegador B inicia sesion con la misma cuenta")
check(cerrada(navegador_a, "/pedidos/"), "la sesion del navegador A queda cerrada")
check("otro navegador" in alertas(navegador_a.get("/auth/login")),
      "y el login de A explica que la cuenta entro en otro navegador")
check(cerrada(navegador_a, "/pedidos/"), "A no recupera la sesion")
check(opera(navegador_b, "/pedidos/"), "B sigue abierta")

recordado = sesion("despachador@sgds.com", "Despacho123*", recordar=True)
galleta = recordado.get_cookie("remember_token").value
sesion("despachador@sgds.com", "Despacho123*")
reabierto = app.test_client()
reabierto.set_cookie("remember_token", galleta, domain="localhost")
check(cerrada(reabierto, "/pedidos/"),
      "la cookie de mantener sesion iniciada de otro navegador tampoco sirve")

print("\n== 4. Cerrar sesion en una pestana y navegar en otra ==")
pestana = sesion("conductor1@sgds.com", "Conductor123*")
copia = app.test_client()
copia.set_cookie("session", pestana.get_cookie("session").value, domain="localhost")
check(opera(pestana, "/conductor/") and opera(copia, "/conductor/"),
      "dos pestanas (la misma cookie) operan")
pestana.get("/auth/logout")
check(cerrada(pestana, "/conductor/"), "la pestana que cerro sesion vuelve al login")
check(cerrada(copia, "/conductor/"), "la otra pestana tambien, aunque conserve la cookie")
check(usuario("conductor1@sgds.com").token_sesion is None, "cerrar sesion borra el token")

print("\n== 5. Tiempo de inactividad configurable ==")
html = admin.get("/admin/configuracion/").data.decode()
check('name="minutos_inactividad"' in html and 'value="30"' in html,
      "Configuracion muestra el tiempo inicial de 30 minutos")
for valor, motivo in (("0", "cero"), ("481", "mas de 480"), ("abc", "texto"), ("", "vacio")):
    r = admin.post("/admin/configuracion/sesion", data={"minutos_inactividad": valor},
                   follow_redirects=True)
    check("tiempo de inactividad" in alertas(r).lower(), f"rechaza {motivo}")
r = admin.post("/admin/configuracion/sesion", data={"minutos_inactividad": "30"},
               follow_redirects=True)
check("ya es de 30 minutos" in alertas(r), "avisa si el valor no cambia")

r = admin.post("/admin/configuracion/sesion", data={"minutos_inactividad": "1"},
               follow_redirects=True)
check("1 minuto sin actividad" in alertas(r), "se reduce a 1 minuto")
with app.app_context():
    registro = db.session.query(Auditoria).filter_by(accion="Editar tiempo de inactividad").one()
    check("de 30 minutos a 1 minuto" in registro.resumen, "el cambio queda en la bitacora")
html = admin.get("/admin/usuarios/").data.decode()
check("js/sesion.js" in html and 'data-segundos="60"' in html,
      "las paginas cargan el aviso de inactividad con 60 segundos")
envejecer("admin@sgds.com", 30)
check(admin.get("/auth/sesion").get_json()["restante"] in (29, 30),
      "el estado informa los segundos que le quedan")
check(admin.get("/auth/sesion").get_json()["restante"] in (29, 30),
      "y consultarlo no cuenta como actividad")

conductor = sesion("conductor1@sgds.com", "Conductor123*")
envejecer("conductor1@sgds.com", 50)
check(opera(conductor, "/conductor/"), "a los 50 s sin actividad la sesion sigue")
check(conductor.get("/auth/sesion").get_json()["restante"] in (59, 60),
      "y esa peticion reinicia el contador")
envejecer("conductor1@sgds.com", 61)
check(conductor.get("/auth/sesion").get_json()["restante"] == 0, "a los 61 s el estado es 0")
check(cerrada(conductor, "/conductor/"), "la siguiente peticion cierra la sesion")
check("1 minuto sin actividad" in alertas(conductor.get("/auth/login")),
      "el login explica que se cerro por inactividad")
check(cerrada(conductor, "/conductor/"), "y no se recupera")

recordado = sesion("conductor1@sgds.com", "Conductor123*", recordar=True)
galleta = recordado.get_cookie("remember_token").value
envejecer("conductor1@sgds.com", 61)
reabierto = app.test_client()
reabierto.set_cookie("remember_token", galleta, domain="localhost")
check(cerrada(reabierto, "/conductor/"),
      "mantener sesion iniciada no salta la inactividad al reabrir el navegador")

r = admin.post("/admin/configuracion/sesion", data={"minutos_inactividad": "120"},
               follow_redirects=True)
check("120 minutos sin actividad" in alertas(r), "se aumenta a 120 minutos")
conductor = sesion("conductor1@sgds.com", "Conductor123*")
envejecer("conductor1@sgds.com", 61 * 60)
check(opera(conductor, "/conductor/"), "con 120 minutos, una hora sin actividad no cierra la sesion")
envejecer("conductor1@sgds.com", 121 * 60)
check(cerrada(conductor, "/conductor/"), "pero 121 minutos si")
admin.post("/admin/configuracion/sesion", data={"minutos_inactividad": "30"})

print("\n== 6. Cambiar el correo desde el perfil ==")
cliente = sesion("cliente@sgds.com", "Cliente123*")
html = cliente.get("/auth/perfil").data.decode()
check('name="correo_nuevo"' in html and 'name="contrasena_correo"' in html,
      "el perfil ofrece cambiar el correo")
r = cliente.post("/auth/perfil/correo", data={"correo_nuevo": "nuevo.cliente@sgds.com",
                                              "contrasena_correo": "Equivocada9*"})
check("no es correcta" in alertas(r) and "Intento fallido 1 de 5" in alertas(r),
      "exige la contrasena actual y el fallo cuenta para el bloqueo")
r = cliente.post("/auth/perfil/correo", data={"correo_nuevo": "admin@sgds.com",
                                              "contrasena_correo": "Cliente123*"})
check("Ya existe una cuenta con este correo" in alertas(r), "no toma el correo de otra cuenta")
r = cliente.post("/auth/perfil/correo", data={"correo_nuevo": "cliente@sgds.com",
                                              "contrasena_correo": "Cliente123*"})
check("el mismo" in alertas(r), "avisa si el correo no cambia")
r = cliente.post("/auth/perfil/correo", data={"correo_nuevo": "<script>@x.com",
                                              "contrasena_correo": "Cliente123*"})
check("Use solo letras" in r.data.decode(), "aplica la lista blanca del correo")
check(usuario("cliente@sgds.com").correo == "cliente@sgds.com", "los intentos invalidos no lo cambian")
r = cliente.post("/auth/perfil/correo", data={"correo_nuevo": "  Nuevo.Cliente@SGDS.com ",
                                              "contrasena_correo": "Cliente123*"},
                 follow_redirects=True)
check("nuevo.cliente@sgds.com" in alertas(r), "cambia el correo, normalizado")
check(opera(cliente, "/portal/"), "la sesion sigue abierta")
check(opera(sesion("nuevo.cliente@sgds.com", "Cliente123*"), "/portal/"), "entra con el correo nuevo")
r = app.test_client().post("/auth/login", data={"correo": "cliente@sgds.com",
                                                 "contrasena": "Cliente123*"})
check(r.status_code == 200 and "Credenciales invalidas" in alertas(r), "el anterior ya no sirve")
with app.app_context():
    registro = db.session.query(Auditoria).filter_by(accion="Cambiar correo propio").one()
    check("cliente@sgds.com a nuevo.cliente@sgds.com" in registro.resumen, "queda en la bitacora")
cliente = sesion("nuevo.cliente@sgds.com", "Cliente123*")
r = cliente.post("/auth/perfil", data={"contrasena_actual": "Cliente123*",
                                       "contrasena_nueva": "1234567891011"})
check("al menos una mayúscula" in r.data.decode(), "la contrasena del perfil sigue la politica")
r = cliente.post("/auth/perfil", data={"contrasena_actual": "Cliente123*",
                                       "contrasena_nueva": "ClienteNueva1*"}, follow_redirects=True)
check(b"Contrasena actualizada" in r.data, "y se actualiza con una que cumple")

conductor = sesion("conductor2@sgds.com", "Conductor123*")
admin.post(f"/admin/usuarios/{id_c2}/alternar-exigencia")
html = conductor.get("/auth/perfil").data.decode()
check('name="correo_nuevo"' not in html, "con contrasena temporal no ofrece cambiar el correo")
r = conductor.post("/auth/perfil/correo", data={"correo_nuevo": "otro.c2@sgds.com",
                                                "contrasena_correo": "Conductor123*"})
check(r.status_code == 302 and "/auth/perfil" in r.headers["Location"]
      and usuario("conductor2@sgds.com").correo == "conductor2@sgds.com",
      "ni lo acepta por POST")
admin.post(f"/admin/usuarios/{id_c2}/alternar-exigencia")

print("\n== 7. Registro de errores ==")
html = admin.get("/admin/errores/").data.decode()
check("No se han registrado errores" in html, "la pantalla empieza vacia")
check("Errores</a>" in html, "esta en el menu de administracion")
check(sesion("despachador@sgds.com", "Despacho123*").get("/admin/errores/").status_code == 403,
      "el gestor no la ve")

app.config["PROPAGATE_EXCEPTIONS"] = False   # como en produccion: pagina 500
r = admin.post("/admin/errores/probar")
html = r.data.decode()
check(r.status_code == 500 and "registro de errores con la referencia" in html,
      "provocar un error muestra la pagina 500 con su referencia")
numero = int(re.search(r"#(\d+)", html).group(1))
with app.app_context():
    error = db.session.get(ErrorSistema, numero)
    check(error is not None and error.tipo == "app.controllers.errores.FalloForzado",
          "el registro guarda la clase del error")
    check(error.metodo == "POST" and error.ruta == "/admin/errores/probar"
          and error.usuario_nombre == usuario("admin@sgds.com").nombre,
          "con la peticion y el usuario")
    check("Traceback" in error.traza and "raise FalloForzado" in error.traza, "y la traza")
html = admin.get("/admin/errores/").data.decode()
check(f"#{numero}" in html and "FalloForzado" in html, "la pantalla lo muestra con el mismo numero")
check(f"Error #{numero}" in ARCHIVO_ERRORES.read_text(encoding="utf-8"),
      "y tambien queda en el archivo de errores")

app.config["PROPAGATE_EXCEPTIONS"] = None    # desarrollo: el error llega al depurador
try:
    admin.post("/admin/errores/probar")
    llego = False
except FalloForzado:
    llego = True
with app.app_context():
    total = db.session.query(ErrorSistema).count()
check(llego and total == 2, "en modo desarrollo el error llega al depurador y tambien se registra")

app.config["PROPAGATE_EXCEPTIONS"] = False
app.config["WTF_CSRF_ENABLED"] = True
r = admin.post("/admin/errores/probar")
app.config["WTF_CSRF_ENABLED"] = False
with app.app_context():
    total = db.session.query(ErrorSistema).count()
check(r.status_code == 400 and total == 2, "sin token CSRF no se provoca nada")

print("\n== 8. Migracion m010 ==")
from migraciones.m010_sesiones_y_errores import aplicar
with app.app_context():
    check(aplicar(verboso=False) == [], "sobre una base al dia no hace nada")
    with db.engine.begin() as conexion:
        conexion.execute(text("DROP TABLE errores_sistema"))
        for tabla, columna in (("usuarios", "token_sesion"), ("usuarios", "sesion_actividad_en"),
                               ("configuracion_negocio", "minutos_inactividad")):
            conexion.execute(text(f"ALTER TABLE {tabla} DROP COLUMN {columna}"))
    check("errores_sistema" not in inspect(db.engine).get_table_names(), "la base queda como antes")
    check(len(aplicar(verboso=False)) == 4, "agrega las tres columnas y la tabla")
    check(aplicar(verboso=False) == [], "volver a ejecutarla no cambia nada")
    db.session.remove()
    from app.services.configuracion import minutos_inactividad
    check(minutos_inactividad() == 30, "la configuracion existente queda con 30 minutos")
admin = sesion("admin@sgds.com", "Admin123*")
check(opera(admin, "/admin/errores/"), "tras la migracion se inicia sesion y opera")


print("\n" + "=" * 55)
print("RESULTADO: " + ("TODAS LAS PRUEBAS PASARON" if not fallos else f"{len(fallos)} FALLAS"))
for f in fallos: print("   - " + f)
sys.exit(1 if fallos else 0)
