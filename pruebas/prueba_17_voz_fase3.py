"""Funciones de voz de la fase 3: clientes, sedes, productos, vehiculos, rutas,
edicion de pedidos (gestor y admin), y usuarios, configuracion y bitacora
(solo admin). Cada accion se prueba con su resumen, sus rechazos y su efecto.

Corre sin internet: Retell y Make se simulan (las firmas X-Retell-Signature se
generan con el propio SDK) y el ruteo usa el respaldo local.
"""

import json
import pathlib
import re
import sys
from datetime import time, timedelta
from types import SimpleNamespace

import requests as _requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _preparar  # noqa: E402  Primero: base de pruebas, sin Make, Retell ni red externa.
from retell.lib.webhook_auth import symmetric

from run import app
from app.extensions import db
from app.models import (Auditoria, Cliente, DireccionCliente, EstadoPedido, EstadoRuta, MotivoFallo,
                        MovimientoInventario, Pedido, PedidoItem, Producto, Rol, Ruta, SesionAsistente,
                        TipoMovimiento, Usuario, Vehiculo, VIGENCIA_SESION)
from app.services import notificaciones
from app.services import ruteo
from app.services.configuracion import centro_distribucion
from app.tiempo import ahora, hoy

fallos = []
def check(c, m):
    print(("  OK   " if c else "  FALLA") + f" {m}")
    if not c: fallos.append(m)

_preparar.reiniciar_base()

CLAVE = "clave-retell-fase3"
OPERACIONES = "operaciones@sgds.invalid"
URL_MAKE = "https://hook.make.invalid/sgds"
URL_CREDENCIALES = "https://hook.make.invalid/credenciales"
app.config.update(RETELL_API_KEY=CLAVE, MAKE_WEBHOOK_URL=URL_MAKE, MAKE_WEBHOOK_KEY="",
                  MAKE_WEBHOOK_CREDENCIALES_URL="", CORREO_OPERACIONES=OPERACIONES,
                  URL_PUBLICA="https://sgds.ejemplo.com")

envios = []
notificaciones.requests.post = lambda url, json=None, timeout=None, headers=None: (
    envios.append((url, json)), SimpleNamespace(raise_for_status=lambda: None))[1]

def _sin_red(*_, **__):
    raise _requests.ConnectionError("sin red en las pruebas")
ruteo._consultar_osrm = _sin_red


# ---- Utilidades ----
retell = app.test_client()
def funcion(ruta, call_id, args=None):
    cuerpo = json.dumps({"call": {"call_id": call_id}, "name": ruta.split("/")[-1], "args": args or {}},
                        ensure_ascii=False)
    r = retell.post(f"/api/asistente/{ruta}", data=cuerpo.encode(),
                    headers={"Content-Type": "application/json",
                             "X-Retell-Signature": symmetric["sign"](cuerpo, CLAVE)})
    return r.status_code, (r.get_json(silent=True) or {})

def dice(ruta, call_id, args=None):
    return funcion(ruta, call_id, args)[1].get("mensaje", "")

def confirma(ruta, call_id, args):
    """Resumen y confirmacion. Devuelve (resumen, respuesta final)."""
    resumen = dice(ruta, call_id, args)
    if not resumen.endswith("¿confirmas?"):
        return resumen, None
    return resumen, dice(ruta, call_id, {**args, "confirmar": True})

def llamada(correo, call_id):
    with app.app_context():
        u = db.session.query(Usuario).filter_by(correo=correo).one()
        db.session.add(SesionAsistente(call_id=call_id, usuario_id=u.id, rol=u.rol,
                                       vence_en=ahora() + VIGENCIA_SESION))
        db.session.commit()
        return u.id

def eventos(tipo, desde=0):
    notificaciones.esperar_envios()
    return [(url, e) for url, e in envios[desde:] if e.get("tipo") == tipo]

def una(modelo, **filtros):
    with app.app_context():
        return db.session.query(modelo).filter_by(**filtros).one_or_none()

def cuenta(modelo):
    with app.app_context():
        return db.session.query(modelo).count()

G, A = "call_fase3_gestor", "call_fase3_admin"
gestor_id = llamada("despachador@sgds.com", G)
admin_id = llamada("admin@sgds.com", A)
D = hoy().strftime("%Y%m%d")


print("\n== 1. Clientes ==")
check(dice("gestor/clientes", G, {"operacion": "crear"}) == "Me falta el nombre del cliente.",
      "crear sin nombre dice que falta")
r = dice("gestor/clientes", G, {"operacion": "crear", "nombre": "Ferreteria Central", "nit": "900111222"})
check(r == "Voy a registrar el cliente Ferreteria Central, NIT 900111222, teléfono sin dato y correo sin "
          "dato. El NIT queda fijo una vez registrado. Después podrás agregarle sus sedes. ¿confirmas?",
      f"el resumen lee nombre, NIT, telefono y correo, y avisa que el NIT queda fijo: {r}")
antes = cuenta(Cliente)
_, final = confirma("gestor/clientes", G, {"operacion": "crear", "nombre": "Ferreteria Central",
                                           "nit": "900111222"})
check(final == "Listo, registré el cliente Ferreteria Central. ¿Le agrego una sede?" and cuenta(Cliente) == antes + 1,
      "con confirmar lo registra y ofrece agregar una sede")
r = dice("gestor/clientes", G, {"operacion": "crear", "nombre": "Otra Ferreteria", "nit": "900111222"})
check("Ya existe un cliente con este NIT" in r and not r.endswith("¿confirmas?"), "el NIT es unico")
r = dice("gestor/clientes", G, {"operacion": "crear", "nombre": "tienda la esquina"})
check("ya existe y no se distingue" in r, "un homonimo sin NIT se rechaza, como en pantalla")
r = dice("gestor/clientes", G, {"operacion": "consultar", "cliente": "ferreteria central"})
check("NIT 900111222" in r and "No tiene sedes registradas" in r and "sin acceso al portal" in r,
      f"consultar lee sus datos: {r}")
check(dice("gestor/clientes", G, {"operacion": "editar", "cliente": "ferreteria central"}).startswith(
      "¿Qué quieres cambiar de Ferreteria Central?"), "editar sin datos pregunta que cambiar")
r, final = confirma("gestor/clientes", G, {"operacion": "editar", "cliente": "ferreteria central",
                                           "telefono": "6015551234"})
check(r == "Voy a cambiar en el cliente Ferreteria Central el teléfono de sin dato a 6015551234. ¿confirmas?"
      and final == "Listo, actualicé el cliente Ferreteria Central."
      and una(Cliente, nombre="Ferreteria Central").telefono == "6015551234",
      f"editar lee el cambio con el valor anterior y lo aplica: {r}")
r = dice("gestor/clientes", G, {"operacion": "editar", "cliente": "ferreteria central", "nit": "800999888"})
check("El NIT queda fijo una vez registrado" in r, "un NIT registrado no cambia")
_, final = confirma("gestor/clientes", G, {"operacion": "crear", "nombre": "Panaderia Sin Nit"})
r, final = confirma("gestor/clientes", G, {"operacion": "editar", "cliente": "panaderia sin nit",
                                           "nit": "901222333"})
check("el NIT de sin dato a 901222333" in r and "El NIT queda fijo una vez registrado." in r
      and una(Cliente, nombre="Panaderia Sin Nit").documento == "901222333",
      "un cliente sin NIT lo registra una vez, con el aviso")
r, final = confirma("gestor/clientes", G, {"operacion": "desactivar", "cliente": "panaderia sin nit"})
check("No se le podrán crear pedidos nuevos" in r and una(Cliente, nombre="Panaderia Sin Nit").activo is False,
      "desactivar avisa y lo desactiva")
r = dice("gestor/pedidos", G, {"operacion": "crear", "cliente": "panaderia sin nit",
                               "productos": [{"producto": "SKU-1001", "cantidad": 1}]})
check("No encontré el cliente" in r and "¿Quieres que lo registre ahora?" in r,
      "un cliente inactivo no se ofrece para pedidos")
check(dice("gestor/clientes", G, {"operacion": "desactivar", "cliente": "panaderia sin nit"})
      == "El cliente Panaderia Sin Nit ya está inactivo.", "desactivar dos veces lo dice sin pedir confirmacion")
_, final = confirma("gestor/clientes", G, {"operacion": "activar", "cliente": "panaderia sin nit"})
check(una(Cliente, nombre="Panaderia Sin Nit").activo is True, "activar lo reactiva")


print("\n== 2. Sedes ==")
check(dice("gestor/sedes", G, {"operacion": "crear", "cliente": "ferreteria central"})
      == "Me falta la dirección de la sede.", "crear sin direccion dice que falta")
r = dice("gestor/sedes", G, {"operacion": "crear", "cliente": "ferreteria central", "direccion": "Calle 80 #20-10",
                             "ventana_inicio": "14:00", "ventana_fin": "09:00"})
check(r == "La hora final debe ser posterior a la inicial.", f"una ventana con inicio despues del fin se rechaza: {r}")
r = dice("gestor/sedes", G, {"operacion": "crear", "cliente": "ferreteria central", "direccion": "Calle 80 #20-10",
                             "latitud": 4.68})
check(not r.endswith("¿confirmas?") and "longitud" in r.lower(), f"una coordenada sola se rechaza: {r}")
r, final = confirma("gestor/sedes", G, {"operacion": "crear", "cliente": "ferreteria central",
                                        "etiqueta": "Bodega", "direccion": "Calle 80 #20-10",
                                        "ventana_inicio": "08:00", "ventana_fin": "12:00",
                                        "latitud": 4.6823, "longitud": -74.0789})
check(r == "Voy a agregar a Ferreteria Central la sede Bodega: Calle 80 #20-10, Bogota; ventana de 08:00 a "
          "12:00, con coordenadas. ¿confirmas?" and final == "Listo, agregué la sede Bodega a Ferreteria Central.",
      f"agregar una sede lee direccion, ciudad, ventana y coordenadas: {r}")
r = dice("gestor/sedes", G, {"operacion": "crear", "cliente": "ferreteria central", "direccion": "calle 80 # 20 10"})
check("ya tiene registrada esa direccion" in r, "una direccion repetida (escrita distinto) se rechaza")
r, final = confirma("gestor/sedes", G, {"operacion": "editar", "cliente": "ferreteria central", "sede": "bodega",
                                        "ventana_fin": "13:30"})
with app.app_context():
    bodega = db.session.query(DireccionCliente).filter_by(etiqueta="Bodega").one()
    ventana = (bodega.ventana_inicio, bodega.ventana_fin)
check("el fin de la ventana de 12:00 a 13:30" in r and ventana == (time(8, 0), time(13, 30)),
      f"editar solo el fin de la ventana conserva el inicio: {r}")
r, final = confirma("gestor/sedes", G, {"operacion": "crear", "cliente": "ferreteria central",
                                        "etiqueta": "Local", "direccion": "Cra 7 #45-10"})
r, final = confirma("gestor/sedes", G, {"operacion": "desactivar", "cliente": "ferreteria central", "sede": "local"})
check("Ya no se ofrecerá para pedidos nuevos" in r, "desactivar una sede avisa que no se ofrece")
r = dice("gestor/pedidos", G, {"operacion": "crear", "cliente": "ferreteria central", "sede": "local",
                               "productos": [{"producto": "SKU-1001", "cantidad": 1}]})
check("No encontré esa sede" in r and "Bodega" in r and "Local" not in r,
      "una sede desactivada no se ofrece para pedidos nuevos")
r = dice("gestor/sedes", G, {"operacion": "consultar", "cliente": "ferreteria central"})
check("2 sedes" in r and "Local: Cra 7 #45-10" in r and "desactivada" in r, f"consultar lista todas: {r}")
confirma("gestor/sedes", G, {"operacion": "activar", "cliente": "ferreteria central", "sede": "local"})
check(una(DireccionCliente, etiqueta="Local").activa, "y se puede reactivar")


print("\n== 3. Productos ==")
check(dice("gestor/productos", G, {"operacion": "crear", "nombre": "Tornillos"}) == "Me falta el SKU.",
      "crear sin SKU dice que falta")
r, final = confirma("gestor/productos", G, {"operacion": "crear", "sku": "tor-100", "nombre": "Tornillos x100",
                                            "stock_minimo": 10, "stock_inicial": 40})
with app.app_context():
    tornillos = db.session.query(Producto).filter_by(sku="TOR-100").one()
    movs = tornillos.movimientos.all()
    datos_tornillos = (tornillos.stock_actual, [(m.tipo, m.cantidad) for m in movs])
check("stock inicial de 40, que entra como entrada de mercancía. Con stock inicial, el SKU queda fijo." in r
      and final == "Listo, creé el producto TOR-100." and datos_tornillos == (40, [(TipoMovimiento.ENTRADA, 40)]),
      f"crear con stock inicial avisa que el SKU queda fijo y lo registra como entrada: {r}")
r = dice("gestor/productos", G, {"operacion": "editar", "producto": "TOR-100", "sku": "TOR-200"})
check("El SKU queda fijo" in r, "con movimientos, el SKU no cambia")
r, final = confirma("gestor/productos", G, {"operacion": "editar", "producto": "TOR-100", "stock_minimo": 15,
                                            "nombre": "Tornillos caja x100"})
check("el nombre de Tornillos x100 a Tornillos caja x100" in r and "el stock mínimo de 10 a 15" in r
      and una(Producto, sku="TOR-100").stock_minimo == 15, f"editar lee y aplica los cambios: {r}")
check("El stock cambia con una entrada o un ajuste." in dice("gestor/productos", G,
      {"operacion": "editar", "producto": "TOR-100"}), "editar sin datos recuerda que el stock no se edita")
check("stock_actual" not in [p for p in __import__("app.controllers.asistente_api", fromlist=["FUNCIONES"])
                             .FUNCIONES[("gestor", "productos")].parametros],
      "productos no tiene un parametro para fijar el stock fuera de entrada y ajuste")
r, final = confirma("gestor/productos", G, {"operacion": "desactivar", "producto": "TOR-100"})
check(una(Producto, sku="TOR-100").activo is False and "No se ofrecerá para pedidos nuevos" in r,
      "desactivar un producto")
confirma("gestor/productos", G, {"operacion": "activar", "producto": "TOR-100"})
check(una(Producto, sku="TOR-100").activo is True, "activarlo")


print("\n== 4. Vehiculos ==")
r = dice("gestor/vehiculos", G, {"operacion": "crear", "placa": "wgt 482"})
check("Ya existe un vehículo con la placa WGT-482" in r, "la placa es unica sin importar espacios ni guiones")
r, final = confirma("gestor/vehiculos", G, {"operacion": "crear", "placa": "abc 123", "tipo": "Camion",
                                            "capacidad_kg": 3000, "capacidad_unidades": 900})
check(r == "Voy a registrar el vehículo de placa ABC 123: Camion, 3000 kilos y 900 unidades, sin conductor. "
          "La placa no se podrá cambiar. ¿confirmas?" and una(Vehiculo, placa="ABC 123") is not None,
      f"crear lee tipo, capacidades y conductor y avisa que la placa es fija: {r}")
r = dice("gestor/vehiculos", G, {"operacion": "editar", "placa": "ABC123", "conductor": "andres"})
check("ya tiene asignado el vehículo WGT-482" in r, "un conductor no tiene dos vehiculos")
with app.app_context():
    c3 = Usuario(nombre="Sergio Vargas", correo="conductor3@sgds.com", rol=Rol.CONDUCTOR, activo=True)
    c3.establecer_contrasena("Conductor123*")
    db.session.add(c3)
    db.session.commit()
r, final = confirma("gestor/vehiculos", G, {"operacion": "editar", "placa": "ABC123", "conductor": "sergio"})
check("el conductor de sin dato a Sergio Vargas" in r and una(Vehiculo, placa="ABC 123").conductor_id is not None,
      f"asignar un conductor: {r}")
r, final = confirma("gestor/vehiculos", G, {"operacion": "editar", "placa": "ABC123", "conductor": "ninguno"})
check("el conductor de Sergio Vargas a sin dato" in r and una(Vehiculo, placa="ABC 123").conductor_id is None,
      "'ninguno' lo deja sin conductor")
r = dice("gestor/vehiculos", G, {"operacion": "desactivar", "placa": "WGT482"})
check("está en una ruta abierta" in r and not r.endswith("¿confirmas?"),
      "no se desactiva un vehiculo en una ruta abierta")
r = dice("gestor/vehiculos", G, {"operacion": "consultar"})
check("3 vehículos" in r and "WGT-482" in r and "en 1 ruta abierta" in r, f"consultar sin placa lista todos: {r}")


print("\n== 5. Rutas ==")
# Un segundo pedido pendiente de hoy, creado por voz en la sede nueva.
_, final = confirma("gestor/pedidos", G, {"operacion": "crear", "cliente": "ferreteria central", "sede": "bodega",
                                          "productos": [{"producto": "SKU-1001", "cantidad": 2}]})
check(final and final.startswith("Listo, creé el pedido"), f"crea un pedido para el cliente y la sede nuevos: {final}")
r = dice("gestor/rutas", G, {"operacion": "consultar", "ruta": "andres"})
check(f"La ruta RUT-{D}-01" in r and "Andres Molina" in r and "WGT-482" in r and "en curso" in r,
      f"consultar por conductor: {r}")
check(dice("gestor/rutas", G, {"operacion": "crear"}) == "Me faltan el conductor y los pedidos.",
      "crear sin datos dice todo lo que falta")
with app.app_context():
    pendientes = [p.codigo for p in db.session.query(Pedido).filter(
        Pedido.estado == EstadoPedido.PENDIENTE, Pedido.ruta_id.is_(None), Pedido.fecha_despacho == hoy())]
r = dice("gestor/rutas", G, {"operacion": "crear", "conductor": "sergio", "pedidos": [f"PED-{D}-001"]})
check("no se puede incluir" in r and not r.endswith("¿confirmas?"), "un pedido ya asignado no entra en otra ruta")
args_ruta = {"operacion": "crear", "conductor": "diego", "todos_los_pendientes": True, "criterio": "ventana"}
r = dice("gestor/rutas", G, args_ruta)
check(r.startswith(f"Voy a crear una ruta para el ") and "con Diego Pardo" in r
      and "Diego Pardo tiene asignado el KLM-317, dímelo si quieres usarlo" in r
      and f"con {len(pendientes)} parada" in r and "respetar ventanas horarias del cliente" in r,
      f"el resumen lee conductor, vehiculo, numero de paradas y criterio: {r}")
args_ruta["vehiculo"] = "klm 317"
r, final = confirma("gestor/rutas", G, args_ruta)
check("con el vehículo KLM-317" in r and final and final.startswith(f"Listo, creé la ruta RUT-{D}-"),
      f"con vehiculo lo crea: {final}")
with app.app_context():
    nueva = db.session.query(Ruta).filter(Ruta.conductor_id != None, Ruta.fecha == hoy(),  # noqa: E711
                                          Ruta.codigo != f"RUT-{D}-01").order_by(Ruta.id.desc()).first()
    codigo_nueva = nueva.codigo
    asignados = sorted(p.codigo for p in nueva.pedidos)
check(asignados == sorted(pendientes) and all(una(Pedido, codigo=c).estado == EstadoPedido.ASIGNADO
                                              for c in asignados),
      "los pedidos pendientes quedan asignados a la ruta nueva")
r, final = confirma("gestor/rutas", G, {"operacion": "recalcular", "ruta": codigo_nueva})
check(f"Voy a recalcular la ruta {codigo_nueva} de Diego Pardo para optimizar distancia total" in r
      and final.startswith(f"Listo, recalculé la ruta {codigo_nueva}"), f"recalcular: {r}")
quitado = asignados[0]
r, final = confirma("gestor/rutas", G, {"operacion": "quitar-parada", "ruta": codigo_nueva, "pedido": quitado})
check("vuelve a pendiente sin ruta" in r and una(Pedido, codigo=quitado).estado == EstadoPedido.PENDIENTE,
      "quitar una parada la devuelve a pendiente")
r = dice("gestor/rutas", G, {"operacion": "eliminar", "ruta": f"RUT-{D}-01"})
check("ya tiene entregas registradas" in r and not r.endswith("¿confirmas?"),
      "no se elimina una ruta con entregas")
restantes = len(asignados) - 1
r, final = confirma("gestor/rutas", G, {"operacion": "eliminar", "ruta": codigo_nueva})
check(f"{restantes} parada" in r and "a pendiente" in r and una(Ruta, codigo=codigo_nueva) is None
      and all(una(Pedido, codigo=c).estado == EstadoPedido.PENDIENTE for c in asignados),
      f"eliminar dice cuantas paradas vuelven a pendiente y las devuelve: {r}")


print("\n== 6. Editar pedidos ==")
pendiente = f"PED-{D}-006"
check(dice("gestor/pedidos", G, {"operacion": "editar", "pedido": pendiente}).startswith(
      f"¿Qué quieres cambiar del pedido {pendiente}? Puedo cambiar la fecha, la sede"),
      "sin datos dice que se puede cambiar segun el estado")
r = dice("gestor/pedidos", G, {"operacion": "editar", "pedido": pendiente,
                               "fecha": (hoy() - timedelta(days=1)).isoformat()})
check("no puede ser anterior a hoy" in r, "una fecha pasada se rechaza")
r = dice("gestor/pedidos", G, {"operacion": "editar", "pedido": pendiente, "sede": "bodega"})
check("No encontré esa sede" in r, "solo una sede del mismo cliente")
manana = hoy() + timedelta(days=1)
r, final = confirma("gestor/pedidos", G, {"operacion": "editar", "pedido": pendiente, "fecha": manana.isoformat(),
                                          "ventana_inicio": "15:00", "observaciones": "Tocar el timbre",
                                          "productos": [{"producto": "SKU-1003", "cantidad": 4}]})
with app.app_context():
    p = db.session.query(Pedido).filter_by(codigo=pendiente).one()
    estado_p = (p.fecha_despacho, p.ventana_inicio, p.ventana_fin, p.observaciones,
                [(i.producto.sku, i.cantidad) for i in p.items], sorted(p.eventos, key=lambda e: e.id)[-1].nota)
check("la ventana a 15:00 - 18:00" in r and "las observaciones a «Tocar el timbre»" in r
      and "los productos a 4 de Bolsa arroz 5 kg" in r and final == f"Listo, actualicé el pedido {pendiente}.",
      f"el resumen lee cada cambio (la ventana conserva su fin): {r}")
check(estado_p[:5] == (manana, time(15, 0), time(18, 0), "Tocar el timbre", [("SKU-1003", 4)])
      and "Registrado por el asistente de voz" in estado_p[5], "y queda en el pedido y su bitacora")
asignado = f"PED-{D}-001"
r = dice("gestor/pedidos", G, {"operacion": "editar", "pedido": asignado, "fecha": manana.isoformat()})
check("solo la ventana, la prioridad y las observaciones" in r, "un pedido asignado no cambia de fecha")
r, final = confirma("gestor/pedidos", G, {"operacion": "editar", "pedido": asignado, "prioridad": 1})
check(f"El pedido ya está en la ruta RUT-{D}-01: después conviene recalcularla" in r
      and final == f"Listo, actualicé el pedido {asignado}. ¿Recalculo la ruta?",
      f"en un asignado advierte recalcular la ruta: {r}")
r = dice("gestor/pedidos", G, {"operacion": "editar", "pedido": f"PED-{D}-002", "prioridad": 1})
check("está entregado y no se puede editar" in r, "un pedido entregado no se edita")


print("\n== 7. Usuarios (solo admin) ==")
check(funcion("admin/usuarios", G, {"operacion": "consultar"})[0] == 403, "el gestor no administra cuentas (403)")
r = dice("admin/usuarios", A, {"operacion": "crear", "nombre": "Nora Diaz", "correo": "nora@sgds.com",
                               "rol": "conductor"})
check("No puedo crear cuentas por voz" in r and "MAKE_WEBHOOK_CREDENCIALES_URL" in r and not r.endswith("¿confirmas?"),
      "sin webhook de credenciales, crear se rechaza en el resumen")
check("No puedo restablecer contraseñas por voz" in dice("admin/usuarios", A,
      {"operacion": "restablecer-contrasena", "usuario": "diego"}), "y restablecer tambien")
app.config["MAKE_WEBHOOK_CREDENCIALES_URL"] = URL_CREDENCIALES
check(dice("admin/usuarios", A, {"operacion": "crear", "nombre": "Nora Diaz"}) ==
      "Me faltan el correo y el rol.", "crear dice lo que falta")
check(dice("admin/usuarios", A, {"operacion": "crear", "nombre": "Nora Diaz", "correo": "nora@sgds.com",
                                 "rol": "cliente"}) == "Me falta el cliente cuyo portal verá.",
      "una cuenta de cliente exige el cliente")
inicio = len(envios)
args_nora = {"operacion": "crear", "nombre": "Nora Diaz", "correo": "nora@sgds.com", "rol": "conductor"}
r, final = confirma("admin/usuarios", A, args_nora)
check(r == "Voy a crear la cuenta de Nora Diaz, con el correo nora@sgds.com, como conductor. La contraseña se "
          "genera sola y le llega solo a ese correo; si no le llega, restablécela en pantalla. ¿confirmas?",
      f"el resumen avisa como llega la contraseña: {r}")
credencial = eventos("credencial_usuario", inicio)
with app.app_context():
    nora = db.session.query(Usuario).filter_by(correo="nora@sgds.com").one()
    clave_ok = credencial and nora.verificar_contrasena(credencial[0][1]["contrasena"])
    registro = db.session.query(Auditoria).order_by(Auditoria.id.desc()).first()
    texto_registro = (registro.resumen or "") + (registro.detalle or "")
check(final == "Listo, creé la cuenta de Nora Diaz. La contraseña va en camino a su correo." and clave_ok,
      "crea la cuenta y la contrasena generada sirve")
check(len(credencial) == 1 and credencial[0][0] == URL_CREDENCIALES
      and credencial[0][1]["correo_destino"] == "nora@sgds.com" and credencial[0][1]["accion"] == "creación",
      "la contrasena viaja solo en credencial_usuario, al webhook de credenciales y al correo del usuario")
contrasena = credencial[0][1]["contrasena"] if credencial else "x" * 12
check(contrasena not in final and contrasena not in texto_registro and registro.accion == "Crear usuario"
      and not any(contrasena in json.dumps(e) for _, e in eventos("accion_asistente", inicio)),
      "no aparece en la respuesta de voz, ni en la auditoria, ni en accion_asistente")
check(eventos("accion_asistente", inicio) and eventos("accion_asistente", inicio)[0][0] == URL_MAKE,
      "la accion se avisa a operaciones por el webhook general")
r = dice("admin/usuarios", A, {"operacion": "editar", "usuario": "nora", "correo": "otra@sgds.com"})
check("solo lo cambia un administrador en pantalla" in r, "el correo de una cuenta no se cambia por voz")
r = dice("admin/usuarios", A, {"operacion": "editar", "usuario": "andres", "rol": "gestor"})
check("1 ruta sin cerrar" in r and not r.endswith("¿confirmas?"),
      f"un conductor con rutas sin cerrar no cambia de rol: {r}")
r = dice("admin/usuarios", A, {"operacion": "desactivar", "usuario": "andres"})
check("sin cerrar" in r and not r.endswith("¿confirmas?"), "ni se desactiva")
r = dice("admin/usuarios", A, {"operacion": "editar", "usuario": "cliente@sgds.com", "rol": "gestor"})
check("no cambian a un rol interno" in r, "una cuenta de cliente no pasa a un rol interno")
with app.app_context():
    sergio = db.session.query(Usuario).filter_by(correo="conductor3@sgds.com").one()
    db.session.query(Vehiculo).filter_by(placa="ABC 123").one().conductor_id = sergio.id
    db.session.commit()
r, final = confirma("admin/usuarios", A, {"operacion": "editar", "usuario": "sergio", "rol": "gestor"})
check("el rol de conductor a gestor logístico" in r and "Además, el vehículo ABC 123 queda sin conductor." in r
      and una(Vehiculo, placa="ABC 123").conductor_id is None
      and una(Usuario, correo="conductor3@sgds.com").rol == Rol.DESPACHADOR,
      f"sin rutas abiertas cambia de rol y el resumen avisa que libera el vehiculo: {r}")
r = dice("admin/usuarios", A, {"operacion": "desactivar", "usuario": "admin@sgds.com"})
check("No puede desactivar su propia cuenta" in r, "nadie se desactiva a si mismo")
check(dice("admin/usuarios", A, {"operacion": "restablecer-contrasena", "usuario": "admin@sgds.com"})
      == "Tu propia contraseña la cambias en pantalla, en Perfil.", "la propia contrasena no se restablece por voz")
inicio = len(envios)
with app.app_context():
    hash_antes = db.session.query(Usuario).filter_by(correo="conductor2@sgds.com").one().contrasena_hash
r, final = confirma("admin/usuarios", A, {"operacion": "restablecer-contrasena", "usuario": "diego"})
credencial = eventos("credencial_usuario", inicio)
with app.app_context():
    diego = db.session.query(Usuario).filter_by(correo="conductor2@sgds.com").one()
    cambio = diego.contrasena_hash != hash_antes and credencial and diego.verificar_contrasena(
        credencial[0][1]["contrasena"])
check("la anterior deja de servir" in r and "si no le llega, restablécela en pantalla" in r and cambio
      and credencial[0][1]["accion"] == "restablecimiento" and credencial[0][1]["contrasena"] not in final,
      "restablecer genera una nueva que solo viaja por credencial_usuario")
r, final = confirma("admin/usuarios", A, {"operacion": "desactivar", "usuario": "nora"})
check("No podrá iniciar sesión" in r and una(Usuario, correo="nora@sgds.com").activo is False,
      "desactivar una cuenta")
r = dice("admin/usuarios", A, {"operacion": "consultar", "usuario": "andres"})
check("Andres Molina es conductor" in r and "WGT-482" in r and "1 ruta sin cerrar" in r, f"consultar: {r}")


print("\n== 8. Configuracion (solo admin) ==")
check(funcion("admin/configuracion", G, {"operacion": "consultar"})[0] == 403, "el gestor no la cambia (403)")
r = dice("admin/configuracion", A, {"operacion": "consultar"})
check("El centro de distribución es" in r and "Otro" in r, f"consultar: {r[:80]}")
with app.app_context():
    centro_antes = centro_distribucion()
r, final = confirma("admin/configuracion", A, {"operacion": "editar-centro", "latitud": 4.6512345})
with app.app_context():
    centro = centro_distribucion()
check("la latitud de 4,6482837 a 4,6512345" in r
      and "Solo afecta a las rutas que se creen o se recalculen después." in r and centro["latitud"] == 4.6512345
      and centro["longitud"] == centro_antes["longitud"],
      f"editar solo la latitud lee las coordenadas completas y conserva lo demas: {r}")
check("ya tiene esos datos" in dice("admin/configuracion", A, {"operacion": "editar-centro", "latitud": 4.6512345}),
      "sin cambios no pide confirmacion")
r = dice("admin/configuracion", A, {"operacion": "editar-centro", "latitud": 120})
check("entre -90 y 90" in r, "una latitud fuera de rango se rechaza")
r, final = confirma("admin/configuracion", A, {"operacion": "agregar-motivo", "motivo": "Via cerrada"})
check(una(MotivoFallo, nombre="Via cerrada") is not None, "agregar un motivo")
check("ya existe" in dice("admin/configuracion", A, {"operacion": "agregar-motivo", "motivo": "via  cerrada"}).lower(),
      "un motivo repetido se rechaza")
r = dice("admin/configuracion", A, {"operacion": "renombrar-motivo", "motivo": "otro", "nuevo_nombre": "Varios"})
check("no se puede renombrar" in r, "'Otro' no se renombra")
r, final = confirma("admin/configuracion", A, {"operacion": "renombrar-motivo", "motivo": "via cerrada",
                                               "nuevo_nombre": "Vía cerrada por obras"})
check("conservan el texto anterior" in r and una(MotivoFallo, nombre="Vía cerrada por obras") is not None,
      "renombrar un motivo")
r, final = confirma("admin/configuracion", A, {"operacion": "desactivar-motivo", "motivo": "obras"})
check(una(MotivoFallo, nombre="Vía cerrada por obras").activo is False, "desactivar un motivo")


print("\n== 9. Bitacora (solo admin) ==")
check(funcion("admin/bitacora", G)[0] == 403, "el gestor no la consulta (403)")
r = dice("admin/bitacora", A)
with app.app_context():
    total = db.session.query(Auditoria).count()
check(r.startswith(f"Hay {total} registros; los 5 más recientes: hoy a las") and "por voz" in r,
      f"sin filtros lee los 5 mas recientes: {r[:120]}")
r = dice("admin/bitacora", A, {"entidad": "vehiculos", "cantidad": 2})
check("sobre vehiculos" in r and r.count("Voy a") == 2 and "vehículo" in r, f"filtra por entidad: {r[:160]}")
r = dice("admin/bitacora", A, {"usuario": "carlos"})
with app.app_context():
    del_gestor = db.session.query(Auditoria).filter_by(usuario_id=gestor_id).count()
check(r.startswith(f"Hay {del_gestor} registros de ") and "gestor logístico" in r, f"filtra por usuario: {r[:120]}")
manana_iso = (hoy() + timedelta(days=1)).isoformat()
check(dice("admin/bitacora", A, {"desde": manana_iso}) == f"No hay registros en la bitácora desde el "
      f"{(hoy() + timedelta(days=1)).strftime('%d/%m')}.", "un rango sin registros lo dice")


print("\n== 10. Auditoria de las acciones de la fase 3 ==")
with app.app_context():
    por_voz = {r.accion for r in db.session.query(Auditoria).filter_by(origen="voz")}
check({"Crear cliente", "Editar cliente", "Agregar sede", "Editar sede", "Crear producto", "Crear vehículo",
       "Crear ruta", "Eliminar ruta", "Editar pedido", "Crear usuario", "Restablecer contraseña",
       "Agregar motivo de fallo", "Editar centro de distribución"} <= por_voz,
      "cada accion confirmada por voz queda en la auditoria con su titulo")


print("\n" + "=" * 55)
print("RESULTADO:", "TODAS LAS PRUEBAS PASARON" if not fallos else f"{len(fallos)} FALLAS")
for f in fallos:
    print("   -", f)
sys.exit(1 if fallos else 0)
