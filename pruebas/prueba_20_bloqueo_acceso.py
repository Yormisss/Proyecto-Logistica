"""Bloqueo del inicio de sesion por intentos fallidos (RNF5).

Tras LOGIN_MAX_INTENTOS contrasenas equivocadas seguidas, el correo queda
bloqueado LOGIN_BLOQUEO_SEGUNDOS aun con la contrasena correcta. Cubre el
contador que ve el usuario, los cuatro roles, el reinicio del contador, que el
bloqueo no revele que correos existen, la contrasena actual del perfil y el
contador en el apartado de contrasenas del administrador.
"""

import pathlib
import re
import sys
from datetime import timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _preparar  # noqa: E402  Primero: base de pruebas, sin Make, Retell ni red externa.
_preparar.reiniciar_base()
from sqlalchemy import inspect

from run import app
from app.extensions import db
from app.models import IntentoAcceso, Rol
from app.services import acceso
from app.tiempo import ahora

fallos = []
def check(cond, msg):
    print(("  OK   " if cond else "  FALLA") + f" {msg}")
    if not cond: fallos.append(msg)

app.config["WTF_CSRF_ENABLED"] = False
MAXIMO = app.config["LOGIN_MAX_INTENTOS"]

CUENTAS = {
    Rol.ADMIN: ("admin@sgds.com", "Admin123*"),
    Rol.DESPACHADOR: ("despachador@sgds.com", "Despacho123*"),
    Rol.CONDUCTOR: ("conductor1@sgds.com", "Conductor123*"),
    Rol.CLIENTE: ("cliente@sgds.com", "Cliente123*"),
}


def intentar(correo, clave):
    """Un intento de login. Devuelve (entro, mensaje mostrado)."""
    c = app.test_client()
    r = c.post("/auth/login", data={"correo": correo, "contrasena": clave})
    m = re.search(r'<div class="alerta alerta-error">(.*?)</div>', r.data.decode())
    return r.status_code == 302, (m.group(1) if m else "")


def sesion(correo, clave):
    c = app.test_client()
    c.post("/auth/login", data={"correo": correo, "contrasena": clave})
    return c


def contador(correo):
    with app.app_context():
        fila = db.session.get(IntentoAcceso, correo)
        return None if fila is None else (fila.fallidos, fila.bloqueado_hasta is not None)


def vencer_bloqueo(correo):
    """Simula que paso el minuto, sin esperarlo."""
    with app.app_context():
        fila = db.session.get(IntentoAcceso, correo)
        fila.bloqueado_hasta = ahora() - timedelta(seconds=1)
        db.session.commit()


def agotar(correo, clave_mala="Equivocada9*"):
    """Falla hasta el bloqueo. Devuelve los mensajes de cada intento."""
    return [intentar(correo, clave_mala)[1] for _ in range(MAXIMO)]


admin = sesion("admin@sgds.com", "Admin123*")

print(f"\n== 1. Contador y bloqueo tras {MAXIMO} fallos, en los cuatro roles ==")
for rol, (correo, clave) in CUENTAS.items():
    mensajes = agotar(correo)
    check(all(f"Intento fallido {i} de {MAXIMO}" in m for i, m in enumerate(mensajes[:-1], 1)),
          f"{rol}: los primeros {MAXIMO - 1} fallos muestran el contador 1..{MAXIMO - 1}")
    check(not any("Acceso no valido" in m for m in mensajes[:-1]),
          f"{rol}: sin bloqueo antes del intento {MAXIMO}")
    check("Acceso no valido" in mensajes[-1] and "Espere 1 minuto" in mensajes[-1],
          f"{rol}: el fallo {MAXIMO} dice acceso no valido y que espere un minuto")

    entro, mensaje = intentar(correo, clave)
    check(not entro and "Acceso no valido" in mensaje,
          f"{rol}: bloqueado, ni la contrasena correcta entra")
    check("Espere 1 minuto" in mensaje, f"{rol}: recien bloqueado, falta un minuto")
    with app.app_context():
        # Al segundo, como `acceso._registrar_fallo`: MySQL redondea los
        # microsegundos de DATETIME y podia guardar un segundo de mas (26).
        db.session.get(IntentoAcceso, correo).bloqueado_hasta = (
            ahora().replace(microsecond=0) + timedelta(seconds=25))
        db.session.commit()
    _, mensaje = intentar(correo, clave)
    check(re.search(r"Espere 2[45] segundos", mensaje) is not None,
          f"{rol}: a mitad del bloqueo informa los segundos que faltan")
    check(contador(correo) == (MAXIMO, True),
          f"{rol}: los intentos durante el bloqueo no suman al contador")

    vencer_bloqueo(correo)
    entro, _ = intentar(correo, clave)
    check(entro, f"{rol}: pasado el minuto entra con la contrasena correcta")
    check(contador(correo) is None, f"{rol}: y el contador vuelve a cero")

print("\n== 2. Entrar bien antes del limite reinicia el contador ==")
correo, clave = CUENTAS[Rol.DESPACHADOR]
for _ in range(MAXIMO - 1):
    intentar(correo, "Equivocada9*")
check(contador(correo) == (MAXIMO - 1, False), f"{MAXIMO - 1} fallos sin bloqueo")
entro, _ = intentar(correo, clave)
check(entro, "con la contrasena correcta entra")
_, mensaje = intentar(correo, "Equivocada9*")
check(f"Intento fallido 1 de {MAXIMO}" in mensaje, "el siguiente fallo vuelve a ser el primero")
intentar(correo, clave)

print("\n== 3. Cumplido el bloqueo, el correo recupera todos sus intentos ==")
correo = "conductor2@sgds.com"
agotar(correo)
vencer_bloqueo(correo)
_, mensaje = intentar(correo, "Equivocada9*")
check(f"Intento fallido 1 de {MAXIMO}" in mensaje and "Acceso no valido" not in mensaje,
      "un fallo despues del bloqueo cuenta desde uno, sin volver a bloquear")
check(contador(correo) == (1, False), "el contador quedo en uno y sin bloqueo")
intentar(correo, "Conductor123*")

print("\n== 4. El bloqueo no revela que correos existen ==")
reales = agotar("conductor2@sgds.com")
inventados = agotar("nadie.existe@sgds.com")
check(reales == inventados, "un correo inexistente recibe exactamente los mismos mensajes")
check(contador("nadie.existe@sgds.com") == (MAXIMO, True), "y tambien queda bloqueado")
vencer_bloqueo("conductor2@sgds.com")
intentar("conductor2@sgds.com", "Conductor123*")

print("\n== 5. El contador es por correo, sin distinguir mayusculas ni espacios ==")
# La sesion del inicio la cerro el login correcto del punto 1 (sesion unica).
admin = sesion("admin@sgds.com", "Admin123*")
intentar("  CONDUCTOR1@SGDS.com ", "Equivocada9*")
check(contador("conductor1@sgds.com") == (1, False),
      "un fallo con mayusculas y espacios cuenta para el mismo correo")
agotar("despachador@sgds.com")
entro, _ = intentar("conductor1@sgds.com", "Conductor123*")
check(entro, "el bloqueo de una cuenta no afecta a otra")
check(admin.get("/admin/").status_code == 200, "ni a las sesiones ya abiertas")

for _ in range(10):
    intentar("o'brien@sgds.com", "Equivocada9*")
with app.app_context():
    check(db.session.get(IntentoAcceso, "o'brien@sgds.com") is None,
          "un correo con caracteres prohibidos no llega a contar intentos")

print("\n== 6. El contador en el apartado de contrasenas del admin ==")
intentar("conductor2@sgds.com", "Equivocada9*")
intentar("conductor2@sgds.com", "Equivocada9*")
html = admin.get("/admin/usuarios/contrasenas").data.decode()
check("<th>Intentos fallidos</th>" in html, "el apartado tiene la columna de intentos fallidos")
fila_c2 = re.search(r"conductor2@sgds\.com.*?</tr>", html, re.S).group(0)
check(f"2 de {MAXIMO}" in fila_c2, "muestra los fallos acumulados de una cuenta")
fila_desp = re.search(r"despachador@sgds\.com.*?</tr>", html, re.S).group(0)
check("Bloqueada" in fila_desp and "s restantes" in fila_desp,
      "muestra la cuenta bloqueada y cuanto le falta")
fila_admin = re.search(r"admin@sgds\.com.*?</tr>", html, re.S).group(0)
check("Bloqueada" not in fila_admin and f" de {MAXIMO}" not in fila_admin,
      "una cuenta sin fallos no muestra contador")
vencer_bloqueo("despachador@sgds.com")
html = admin.get("/admin/usuarios/contrasenas").data.decode()
fila_desp = re.search(r"despachador@sgds\.com.*?</tr>", html, re.S).group(0)
check("Bloqueada" not in fila_desp, "un bloqueo cumplido ya no se muestra")
intentar("despachador@sgds.com", "Despacho123*")
intentar("conductor2@sgds.com", "Conductor123*")

print("\n== 7. La contrasena actual del perfil cuenta para el mismo bloqueo ==")
c2 = sesion("conductor2@sgds.com", "Conductor123*")
mensajes = []
for _ in range(MAXIMO):
    r = c2.post("/auth/perfil", data={"contrasena_actual": "Equivocada9*",
                                      "contrasena_nueva": "NuevaClave9*"})
    m = re.search(r'<div class="alerta alerta-error">(.*?)</div>', r.data.decode())
    mensajes.append(m.group(1) if m else "")
check(all(f"Intento fallido {i} de {MAXIMO}" in m for i, m in enumerate(mensajes[:-1], 1)),
      "los fallos en el perfil muestran el contador")
check("Acceso no valido" in mensajes[-1], f"el fallo {MAXIMO} bloquea")
r = c2.post("/auth/perfil", data={"contrasena_actual": "Conductor123*",
                                  "contrasena_nueva": "NuevaClave9*"})
check(b"Acceso no valido" in r.data, "bloqueado, ni la actual correcta permite cambiarla")
entro, mensaje = intentar("conductor2@sgds.com", "Conductor123*")
check(not entro and "Acceso no valido" in mensaje, "y el login de esa cuenta queda bloqueado")
vencer_bloqueo("conductor2@sgds.com")
r = c2.post("/auth/perfil", data={"contrasena_actual": "Conductor123*",
                                  "contrasena_nueva": "NuevaClave9*"}, follow_redirects=True)
check(b"Contrasena actualizada" in r.data, "pasado el minuto, la cambia normalmente")
check(contador("conductor2@sgds.com") is None, "y el contador vuelve a cero")

print("\n== 8. Contadores viejos se descartan ==")
with app.app_context():
    db.session.add(IntentoAcceso(correo="olvidado@sgds.com", fallidos=MAXIMO - 1,
                                 ultimo_fallo_en=ahora() - timedelta(days=2)))
    db.session.commit()
_, mensaje = intentar("olvidado@sgds.com", "Equivocada9*")
check(f"Intento fallido 1 de {MAXIMO}" in mensaje,
      "fallos de hace mas de un dia no cuentan: el siguiente es el primero")

print("\n== 9. El limite y la espera salen de la configuracion ==")
app.config["LOGIN_MAX_INTENTOS"], app.config["LOGIN_BLOQUEO_SEGUNDOS"] = 3, 90
mensajes = [intentar("config@sgds.com", "Equivocada9*")[1] for _ in range(3)]
check("Intento fallido 2 de 3" in mensajes[1], "con un maximo de 3, el contador va de 3")
check("Acceso no valido" in mensajes[2] and "Espere 90 segundos" in mensajes[2],
      "y bloquea al tercero, con la espera configurada")
app.config["LOGIN_MAX_INTENTOS"], app.config["LOGIN_BLOQUEO_SEGUNDOS"] = MAXIMO, 60
with app.app_context():
    check(acceso.mensaje_bloqueo(60).endswith("Espere 1 minuto para volver a intentar iniciar sesion."),
          "con la configuracion por defecto la espera es de un minuto")

print("\n== 10. Migracion de la tabla de intentos ==")
from migraciones.m009_intentos_acceso import aplicar
with app.app_context():
    check(aplicar(verboso=False) is False, "sobre una base al dia no hace nada")
    IntentoAcceso.__table__.drop(db.engine)
    check("intentos_acceso" not in inspect(db.engine).get_table_names(), "sin la tabla")
    check(aplicar(verboso=False) is True, "la crea")
    check(aplicar(verboso=False) is False, "volver a ejecutarla no cambia nada")
_, mensaje = intentar("admin@sgds.com", "Equivocada9*")
check(f"Intento fallido 1 de {MAXIMO}" in mensaje, "y el login vuelve a contar intentos")


print("\n" + "=" * 55)
print("RESULTADO: " + ("TODAS LAS PRUEBAS PASARON" if not fallos else f"{len(fallos)} FALLAS"))
for f in fallos: print("   - " + f)
sys.exit(1 if fallos else 0)
