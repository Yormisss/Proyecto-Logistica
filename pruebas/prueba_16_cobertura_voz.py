"""Cobertura por voz de las acciones de pantalla y dialogo de formulario.

Toda ruta POST de la pantalla debe tener una funcion del asistente que la
cubra, con los mismos roles, o estar en la lista aprobada de exclusiones.
Lo que todavia falta se lista como pendiente, con su fase.
"""

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from retell.lib.webhook_auth import symmetric

from run import app
from app.asistentes import cobertura
from app.controllers.asistente_api import FUNCIONES
from app.controllers.asistente_api.comun import frase_datos_invalidos, me_falta
from app.extensions import db
from app.models import SesionAsistente, Usuario, VIGENCIA_SESION
from app.services.clientes import validar_cliente
from app.services.validacion import DatosInvalidos
from app.tiempo import ahora

fallos = []
def check(c, m):
    print(("  OK   " if c else "  FALLA") + f" {m}")
    if not c: fallos.append(m)

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _preparar import reiniciar_base
reiniciar_base()

CLAVE = "clave-retell-prueba"
app.config.update(RETELL_API_KEY=CLAVE, MAKE_WEBHOOK_URL="", CORREO_OPERACIONES="")

# Exclusiones aprobadas por el usuario. Agregar otra exige cambiar esta lista
# a conciencia (y preguntarlo antes).
EXCLUSIONES_APROBADAS = {"auth.login", "auth.perfil", "asistente.llamada",
                         "pedidos.importar", "pedidos.confirmar_importacion"}


print("\n== 1. Mapa de cobertura ==")
rutas = cobertura.rutas_post_de_pantalla(app)
sin_mapa = sorted(set(rutas) - set(cobertura.COBERTURA))
check(not sin_mapa, f"toda ruta POST de pantalla esta en el mapa {sin_mapa}")
sobrantes = sorted(set(cobertura.COBERTURA) - set(rutas))
check(not sobrantes, f"el mapa no nombra rutas que ya no existen {sobrantes}")

for endpoint, entrada in sorted(cobertura.COBERTURA.items()):
    if endpoint not in rutas:
        continue
    resuelta = bool(entrada.voz) or bool(entrada.excluida)
    check(resuelta or entrada.pendiente, f"{endpoint}: tiene funcion de voz, exclusion o pendiente")
    check(not (entrada.voz and entrada.excluida), f"{endpoint}: no esta a la vez cubierta y excluida")

excluidas = {e for e, c in cobertura.COBERTURA.items() if c.excluida}
check(excluidas == EXCLUSIONES_APROBADAS,
      f"solo se excluye lo aprobado (login, perfil, iniciar la llamada, importar CSV) {sorted(excluidas)}")
check(all(len(cobertura.COBERTURA[e].excluida) >= 20 for e in excluidas), "cada exclusion dice por que")


print("\n== 2. Las funciones nombradas existen y sirven a los mismos roles ==")
for endpoint, entrada in sorted(cobertura.COBERTURA.items()):
    if not entrada.voz or endpoint not in rutas:
        continue
    roles_voz = set()
    for referencia in entrada.voz:
        espacio, nombre, operacion = cobertura.separar(referencia)
        funcion = FUNCIONES.get((espacio, nombre))
        check(funcion is not None, f"{endpoint}: existe la funcion {referencia}")
        if funcion is None:
            continue
        check(funcion.accion, f"{endpoint}: {referencia} modifica datos y pide confirmacion")
        if operacion:
            opciones = funcion.parametros.get("operacion", {}).get("enum", [])
            check(operacion in opciones, f"{endpoint}: {referencia} acepta la operacion {operacion}")
            check(operacion in funcion.operaciones and funcion.operaciones[operacion].modifica,
                  f"{endpoint}: la operacion {operacion} modifica datos y pide confirmacion")
        roles_voz |= set(funcion.roles)
    roles_pantalla = set(getattr(app.view_functions[endpoint], "roles_permitidos", ()))
    faltan = roles_pantalla - roles_voz
    check(not faltan or entrada.pendiente,
          f"{endpoint}: los roles de pantalla {sorted(roles_pantalla)} la tienen por voz"
          + (f" (falta {sorted(faltan)})" if faltan else ""))

sin_roles = sorted(e for e in rutas if e not in excluidas
                   and not getattr(app.view_functions[e], "roles_permitidos", None))
check(not sin_roles, f"toda pantalla cubierta declara sus roles con requiere_rol {sin_roles}")


print("\n== 3. Pendientes ==")
pendientes = sorted((e, c.pendiente) for e, c in cobertura.COBERTURA.items() if c.pendiente)
for endpoint, que in pendientes:
    print(f"  ...  {endpoint}: {que}")
check(cobertura.PENDIENTES_PERMITIDOS or not pendientes,
      f"al cerrar la fase 5 no queda nada pendiente ({len(pendientes)} pendientes)")
check(all(c.pendiente.startswith("Fase") for c in cobertura.COBERTURA.values() if c.pendiente),
      "cada pendiente dice en que fase se resuelve")


print("\n== 4. Dialogo de formulario ==")
check(me_falta(["el cliente"]) == "Me falta el cliente.", "me_falta con un dato")
check(me_falta(["las cantidades"]) == "Me faltan las cantidades.", "concuerda con un dato en plural")
check(me_falta(["el cliente", "los productos", "el cliente"]) == "Me faltan el cliente y los productos.",
      "me_falta enumera sin repetir, en plural")

with app.app_context():
    try:
        validar_cliente({"correo": "esto-no-es-correo"})
        error = None
    except DatosInvalidos as e:
        error = e
check(error is not None and frase_datos_invalidos(error)
      == "Me falta el nombre del cliente. El correo no es válido.",
      f"las reglas de pantalla se leen en voz alta: {frase_datos_invalidos(error) if error else None}")

retell = app.test_client()
def funcion(ruta, call_id, args=None):
    cuerpo = json.dumps({"call": {"call_id": call_id}, "args": args or {}}, ensure_ascii=False)
    r = retell.post(f"/api/asistente/{ruta}", data=cuerpo.encode(),
                    headers={"Content-Type": "application/json",
                             "X-Retell-Signature": symmetric["sign"](cuerpo, CLAVE)})
    return (r.get_json(silent=True) or {}).get("mensaje", "")

with app.app_context():
    gestor = db.session.query(Usuario).filter_by(correo="despachador@sgds.com").one()
    db.session.add(SesionAsistente(call_id="call_cobertura", usuario_id=gestor.id, rol=gestor.rol,
                                   vence_en=ahora() + VIGENCIA_SESION))
    db.session.commit()

check(funcion("gestor/pedidos", "call_cobertura", {"operacion": "crear"}) == "Me faltan el cliente y los productos con sus cantidades.",
      "pedidos:crear sin datos dice todo lo que falta de una vez")
check(funcion("gestor/pedidos", "call_cobertura").startswith("¿Qué quieres hacer? Con pedidos puedo:"),
      "sin operacion, la funcion agrupada pregunta que hacer")
check(funcion("gestor/pedidos", "call_cobertura", {"operacion": "crear", "cliente": "Supermercado El Portal"})
      == "Me faltan los productos con sus cantidades.", "con el cliente, dice solo lo que falta")


print("\n" + "=" * 55)
print("RESULTADO:", "TODAS LAS PRUEBAS PASARON" if not fallos else f"{len(fallos)} FALLAS")
sys.exit(1 if fallos else 0)
