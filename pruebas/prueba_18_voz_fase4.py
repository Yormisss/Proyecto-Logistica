"""Funciones de voz de la fase 4 y pruebas de entrega de solo agregar.

Conductor: iniciar-ruta, parada:entregar (con nombre y documento de quien
recibe) y mi-historial; sus acciones quedan en la auditoria sin correo. Gestor:
los indicadores de la analitica y atender-solicitud. Admin: la bitacora con lo
anterior. Ademas, la migracion m007 y un pedido que falla, se reintenta y se
entrega conservando cada prueba de entrega.

Corre sin internet: Retell y Make se simulan (las firmas X-Retell-Signature se
generan con el propio SDK) y el ruteo usa el respaldo local.
"""

import json
import pathlib
import re
import sys
from datetime import time
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _preparar  # noqa: E402  Primero: base de pruebas, sin Make, Retell ni red externa.
from retell.lib.webhook_auth import symmetric
from sqlalchemy import inspect, text

from run import app
from app.extensions import db
from app.models import (Auditoria, Cliente, EstadoPedido, EventoPedido, Pedido, Producto, PruebaEntrega, Rol,
                        Ruta, SesionAsistente, SolicitudContacto, Usuario, VIGENCIA_SESION)
from app.services import analitica, notificaciones
from app.services.despacho import NOTA_ASISTENTE, ruta_del_dia
from app.services.solicitudes import registrar_solicitud
from app.tiempo import ahora, hoy

fallos = []
def check(c, m):
    print(("  OK   " if c else "  FALLA") + f" {m}")
    if not c: fallos.append(m)

_preparar.reiniciar_base()

CLAVE = "clave-retell-fase4"
URL_MAKE = "https://hook.make.invalid/sgds"
TOKEN = "token-resumen-fase4"
app.config.update(RETELL_API_KEY=CLAVE, MAKE_WEBHOOK_URL=URL_MAKE, MAKE_WEBHOOK_KEY="",
                  MAKE_WEBHOOK_CREDENCIALES_URL="", CORREO_OPERACIONES="operaciones@sgds.invalid",
                  AUTOMATIZACION_TOKEN=TOKEN, WTF_CSRF_ENABLED=False)

envios = []
notificaciones.requests.post = lambda url, json=None, timeout=None, headers=None: (
    envios.append((url, json)), SimpleNamespace(raise_for_status=lambda: None))[1]


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

def confirma(ruta, call_id, args=None):
    """Resumen y confirmacion. Devuelve (resumen, respuesta final)."""
    args = args or {}
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
    return [e for _, e in envios[desde:] if e.get("tipo") == tipo]

def auditoria(accion):
    with app.app_context():
        return db.session.query(Auditoria).filter_by(accion=accion).order_by(Auditoria.id.desc()).first()

def sesion_web(correo, clave):
    c = app.test_client()
    c.post("/auth/login", data={"correo": correo, "contrasena": clave})
    return c

def parada_de(orden):
    with app.app_context():
        ruta = ruta_del_dia(conductor_id)
        pedido = next(p for p in ruta.pedidos if p.orden_en_ruta == orden)
        return SimpleNamespace(id=pedido.id, codigo=pedido.codigo, estado=pedido.estado,
                               cliente=pedido.cliente_nombre, cliente_id=pedido.cliente_id,
                               unidades=sum(i.cantidad for i in pedido.items),
                               items=[(i.producto_id, i.cantidad) for i in pedido.items])

C, C2, C3, G, A = "call_f4_cond", "call_f4_cond2", "call_f4_cond3", "call_f4_gestor", "call_f4_admin"
conductor_id = llamada("conductor1@sgds.com", C)
llamada("conductor2@sgds.com", C2)
gestor_id = llamada("despachador@sgds.com", G)
llamada("admin@sgds.com", A)
with app.app_context():
    nuevo = Usuario(nombre="Conductor Nuevo", correo="conductor3@sgds.invalid", rol=Rol.CONDUCTOR, activo=True)
    nuevo.establecer_contrasena("Conductor123*")
    db.session.add(nuevo)
    db.session.commit()
llamada("conductor3@sgds.invalid", C3)


print("\n== 1. Migracion m007: pruebas de entrega sin indice unico ==")
from migraciones.m007_pruebas_entrega_historial import INDICE, aplicar

def indices_pruebas():
    return {i["name"]: (bool(i.get("unique")), i["column_names"])
            for i in inspect(db.engine).get_indexes("pruebas_entrega")}

with app.app_context():
    check(not any(aplicar(verboso=False).values()), "sobre una base al dia no hace nada")
    check(indices_pruebas().get(INDICE) == (False, ["pedido_id"])
          and not any(u for u, cols in indices_pruebas().values() if cols == ["pedido_id"]),
          "el esquema nuevo tiene el indice normal sobre pedido_id y ninguno unico")
    filas_antes = db.session.query(PruebaEntrega).count()
    # La sesion abierta retiene el bloqueo de metadatos de la tabla: sin cerrarla,
    # el ALTER TABLE esperaria indefinidamente.
    db.session.remove()
    # Base anterior: el unico de pedido_id (con el nombre que le da MySQL) y sin el normal.
    with db.engine.begin() as conexion:
        conexion.execute(text("SET SESSION lock_wait_timeout = 10"))
        conexion.execute(text("ALTER TABLE pruebas_entrega ADD UNIQUE INDEX pedido_id (pedido_id)"))
        conexion.execute(text(f"DROP INDEX {INDICE} ON pruebas_entrega"))
    db.session.remove()
    hecho = aplicar(verboso=False)
    check(hecho == {"indices": [INDICE], "unicos_quitados": ["pedido_id"]},
          f"sobre la base anterior crea el indice normal y quita el unico ({hecho})")
    check(db.session.query(PruebaEntrega).count() == filas_antes and filas_antes > 0,
          f"conserva las {filas_antes} pruebas ya registradas")
    check(indices_pruebas().get(INDICE) == (False, ["pedido_id"]) and "pedido_id" not in indices_pruebas(),
          "queda solo el indice normal, que sigue sirviendo a la clave ajena")
    check(not any(aplicar(verboso=False).values()), "volver a ejecutarla no cambia nada")


print("\n== 2. Conductor: iniciar-ruta ==")
check(dice("conductor/iniciar-ruta", C2) == "No tienes una ruta activa para hoy.",
      "sin ruta de hoy lo dice sin pedir confirmacion")
with app.app_context():
    ruta = ruta_del_dia(conductor_id)
    codigo_ruta = ruta.codigo
    asignadas = sorted(p.orden_en_ruta for p in ruta.pedidos if p.estado == EstadoPedido.ASIGNADO)
    en_ruta_antes = {p.orden_en_ruta for p in ruta.pedidos if p.estado == EstadoPedido.EN_RUTA}
check(asignadas == [2], f"la ruta sembrada de conductor1 tiene asignada la parada 2 ({asignadas})")
envios_antes = len(envios)
r = dice("conductor/iniciar-ruta", C)
check(r == f"Voy a iniciar tu ruta {codigo_ruta}: 1 parada pasa a en camino. ¿confirmas?",
      f"el resumen dice la ruta y cuantas paradas pasan a en camino: {r}")
check(parada_de(2).estado == EstadoPedido.ASIGNADO, "el resumen no cambia nada")
r = dice("conductor/iniciar-ruta", C, {"confirmar": True})
check(r == f"Listo, la ruta {codigo_ruta} quedó iniciada con 1 parada en camino.", f"con confirmar la inicia: {r}")
with app.app_context():
    ruta = db.session.query(Ruta).filter_by(codigo=codigo_ruta).one()
    check(all(p.estado != EstadoPedido.ASIGNADO for p in ruta.pedidos) and ruta.iniciada_en is not None,
          "todas las paradas asignadas quedan en camino y la ruta queda iniciada")
    evento = (db.session.query(EventoPedido).filter_by(pedido_id=parada_de(2).id, estado_nuevo=EstadoPedido.EN_RUTA)
              .order_by(EventoPedido.id.desc()).first())
    check(evento.nota == f"Inicio de la ruta {codigo_ruta}" and evento.usuario_id == conductor_id,
          "con el mismo evento que el boton de la pantalla")
registro = auditoria("Iniciar ruta")
check(registro is not None and registro.origen == "voz" and registro.entidad == "ruta"
      and registro.resultado == "exito" and registro.usuario_id == conductor_id,
      "queda en la auditoria, por voz, sobre la ruta")
check(not eventos("accion_asistente", envios_antes), "sin el correo accion_asistente (accion de rutina)")
check(dice("conductor/iniciar-ruta", C) == f"La ruta {codigo_ruta} no tiene paradas asignadas por iniciar.",
      "iniciarla otra vez lo dice sin pedir confirmacion")


print("\n== 3. Conductor: parada:entregar ==")
parada5 = parada_de(5)
check(parada5.estado == EstadoPedido.EN_RUTA, "la parada 5 esta en camino")
with app.app_context():
    stock_antes = {pid: db.session.get(Producto, pid).stock_actual for pid, _ in parada5.items}
ENTREGAR = {"operacion": "entregar", "orden": 5}
check(dice("conductor/parada", C, ENTREGAR) == "Me faltan el nombre de quien recibe y su documento.",
      "sin nombre ni documento dice que faltan los dos")
check(dice("conductor/parada", C, {**ENTREGAR, "recibe": "Ana Ruiz"}) == "Me falta su documento.",
      "sin documento lo pide")
check(dice("conductor/parada", C, {**ENTREGAR, "documento": "1020304050"}) == "Me falta el nombre de quien recibe.",
      "sin nombre lo pide")
r = dice("conductor/parada", C, {**ENTREGAR, "recibe": "Ana Ruiz", "documento": "1" * 41})
check("el documento hasta 40" in r and not r.endswith("¿confirmas?"), "un documento muy largo se rechaza")
DATOS = {**ENTREGAR, "recibe": "Ana Ruiz", "documento": "1020304050"}
r = dice("conductor/parada", C, DATOS)
check(r == f"Voy a confirmar la entrega de la parada 5, {parada5.cliente}, recibida por Ana Ruiz, documento "
          f"1020304050. Se descuentan {parada5.unidades} unidades del inventario. ¿confirmas?",
      f"el resumen lee quien recibe, su documento y las unidades: {r}")
check(parada_de(5).estado == EstadoPedido.EN_RUTA, "el resumen no cambia nada")
envios_antes = len(envios)
r = dice("conductor/parada", C, {**DATOS, "confirmar": True})
check(r == f"Listo, registré la entrega de la parada 5, {parada5.cliente}, recibida por Ana Ruiz. "
          f"Se descontaron {parada5.unidades} unidades del inventario.", f"con confirmar la registra: {r}")
with app.app_context():
    pedido = db.session.get(Pedido, parada5.id)
    prueba = pedido.prueba_entrega
    check(pedido.estado == EstadoPedido.ENTREGADO and pedido.inventario_descontado,
          "el pedido queda ENTREGADO con el inventario descontado")
    check(all(db.session.get(Producto, pid).stock_actual == stock_antes[pid] - cant for pid, cant in parada5.items),
          "descuenta del stock lo mismo que la pantalla")
    check((prueba.receptor_nombre, prueba.receptor_documento, prueba.observacion, prueba.latitud, prueba.longitud)
          == ("Ana Ruiz", "1020304050", "Registrada por el asistente de voz, sin ubicación", None, None),
          "la prueba guarda nombre y documento, sin ubicacion, y lo dice en la observacion")
    evento = sorted(pedido.eventos, key=lambda e: e.id)[-1]
    check(evento.estado_nuevo == EstadoPedido.ENTREGADO and evento.nota == NOTA_ASISTENTE,
          "el evento lleva la nota del asistente")
registro = auditoria("Confirmar entrega")
check(registro is not None and registro.origen == "voz" and registro.entidad == "pedido"
      and "recibida por Ana Ruiz" in registro.resumen, "queda en la auditoria con su resumen")
check(not eventos("accion_asistente", envios_antes), "sin el correo accion_asistente (accion de rutina)")
r = dice("conductor/parada", C, DATOS)
check(r.startswith("No pude confirmar la entrega de la parada 5.") and "Entregado" in r,
      "una parada ya entregada no se vuelve a entregar")
check(funcion("conductor/parada", G, DATOS)[0] == 403, "el gestor no puede llamar las funciones del conductor")


print("\n== 4. Pruebas de entrega de solo agregar: falla, se reintenta y se entrega ==")
parada2 = parada_de(2)
with app.app_context():
    cliente = db.session.get(Cliente, parada2.cliente_id)
    cliente.correo = "portal@cliente.invalid"
    db.session.get(Pedido, parada2.id).ventana_fin = time(23, 59)
    db.session.commit()
    causas_antes = sum(f["cantidad"] for f in analitica.motivos_fallo(1))
    ventana_antes = analitica.cumplimiento_ventana(1)["total"]

# La parada 1 (fallida en la semilla) se reintenta primero: si no, al fallar la 2
# todas quedarian cerradas y la ruta se finalizaria.
_, final = confirma("conductor/parada", C, {"operacion": "en-camino", "orden": 1})
check(final is not None and parada_de(1).estado == EstadoPedido.EN_RUTA, "la parada 1 se reintenta por voz")
envios_antes = len(envios)
FALLO_OBS = {"operacion": "fallo", "orden": 2, "motivo": "cliente ausente",
             "observacion": "Nadie abrio, se dejo aviso en porteria"}
r = dice("conductor/parada", C, {**FALLO_OBS, "observacion": "x" * 1000})
check(r.startswith("La observación es muy larga") and not r.endswith("¿confirmas?"),
      "una observacion dictada demasiado larga se rechaza")
r, _ = confirma("conductor/parada", C, FALLO_OBS)
check(r.endswith("como no entregada por: Cliente ausente. Observación: Nadie abrio, se dejo aviso en porteria. "
                 "¿confirmas?"), f"el resumen del fallo lee la observacion dictada: {r}")
confirma("conductor/parada", C, {"operacion": "en-camino", "orden": 2})
confirma("conductor/parada", C, {"operacion": "fallo", "orden": 2, "motivo": "direccion incorrecta"})
fallidos = [e for e in eventos("pedido_estado", envios_antes) if e["estado"] == EstadoPedido.FALLIDO]
check([e["motivo"] for e in fallidos] == ["Cliente ausente", "Direccion incorrecta"],
      f"cada evento FALLIDO lleva el motivo de su intento ({[e['motivo'] for e in fallidos]})")
resumen = app.test_client().get("/api/automatizacion/resumen-diario",
                                headers={"X-Automatizacion-Token": TOKEN}).get_json()
fila = next((f for f in resumen["fallidos_para_reprogramar"] if f["codigo"] == parada2.codigo), None)
check(fila is not None and fila["motivo"] == "Direccion incorrecta",
      f"el resumen diario usa el motivo del intento mas reciente ({fila and fila['motivo']})")
r = dice("gestor/fallidos-hoy", G)
check(f"{parada2.codigo} de {parada2.cliente}, por Direccion incorrecta" in r,
      "fallidos de hoy lee el motivo mas reciente")
portal = sesion_web("cliente@sgds.com", "Cliente123*")
html = portal.get(f"/portal/pedido/{parada2.id}").data.decode()
check("Direccion incorrecta" in html and "Cliente ausente" not in html,
      "el portal muestra el motivo del intento mas reciente")

confirma("conductor/parada", C, {"operacion": "en-camino", "orden": 2})
r, final = confirma("conductor/parada", C, {"operacion": "entregar", "orden": 2, "recibe": "Luis Pardo",
                                            "documento": "79888777", "observacion": "Recibido en bodega."})
check(r.endswith("del inventario. Observación: Recibido en bodega. ¿confirmas?"),
      f"el resumen de la entrega lee la observacion dictada: {r}")
check(final is not None and final.startswith("Listo, registré la entrega de la parada 2"),
      "tras el reintento, la entrega se confirma por voz")
with app.app_context():
    pedido = db.session.get(Pedido, parada2.id)
    observaciones = [p.observacion for p in pedido.pruebas_entrega]
    check(observaciones == [
        "Registrada por el asistente de voz, sin ubicación. Observación: Nadie abrio, se dejo aviso en porteria",
        "Registrada por el asistente de voz, sin ubicación",
        "Registrada por el asistente de voz, sin ubicación. Observación: Recibido en bodega."],
          f"cada prueba por voz lleva el texto fijo y, si se dicto, la observacion despues ({observaciones})")
    pruebas = [(p.motivo_fallo, p.receptor_nombre) for p in pedido.pruebas_entrega]
    check(pruebas == [("Cliente ausente", None), ("Direccion incorrecta", None), (None, "Luis Pardo")],
          f"el pedido conserva las tres pruebas, en orden ({pruebas})")
    check(pedido.estado == EstadoPedido.ENTREGADO and pedido.prueba_entrega.receptor_nombre == "Luis Pardo"
          and pedido.prueba_entrega.motivo_fallo is None, "la prueba del pedido es la mas reciente: la entrega")
    causas = analitica.motivos_fallo(1)
    check(sum(f["cantidad"] for f in causas) == causas_antes + 2,
          "las causas de fallo cuentan los dos intentos fallidos, aunque el pedido se entrego")
    check(analitica.cumplimiento_ventana(1)["total"] == ventana_antes + 1,
          "el cumplimiento de ventana cuenta la entrega una sola vez")
html = portal.get(f"/portal/pedido/{parada2.id}").data.decode()
check("Luis Pardo" in html and "Direccion incorrecta" not in html,
      "el portal muestra la prueba de la entrega, no la de un intento anterior")
gestor_web = sesion_web("despachador@sgds.com", "Despacho123*")
html = gestor_web.get(f"/pedidos/{parada2.id}").data.decode()
check("Luis Pardo" in html and "79888777" in html, "el detalle del pedido muestra la prueba mas reciente")
resumen = app.test_client().get("/api/automatizacion/resumen-diario",
                                headers={"X-Automatizacion-Token": TOKEN}).get_json()
check(all(f["codigo"] != parada2.codigo for f in resumen["fallidos_para_reprogramar"]),
      "ya entregado, sale de los fallidos por reprogramar del resumen diario")
r = dice("gestor/causas-fallo", G, {"dias": 7})
with app.app_context():
    total7 = sum(f["cantidad"] for f in analitica.motivos_fallo(7))
check(f"hubo {total7} entregas fallidas" in r, f"causas-fallo por voz suma todos los intentos: {r[:80]}")


print("\n== 5. Conductor: mi-historial ==")
r = dice("conductor/mi-historial", C2)
with app.app_context():
    u2 = db.session.query(Usuario).filter_by(correo="conductor2@sgds.com").one()
    ultimas = (db.session.query(Ruta).filter_by(conductor_id=u2.id)
               .order_by(Ruta.fecha.desc(), Ruta.codigo.desc()).limit(5).all())
    codigos = [ru.codigo for ru in ultimas]
    primera = ultimas[0]
    texto_primera = (f"la {primera.codigo} del ", f"{primera.estado_etiqueta.lower()}, con "
                     f"{primera.total_paradas} paradas: {primera.contar(EstadoPedido.ENTREGADO)} entregadas")
check(r.startswith("Tus últimas 5 rutas: ") and [c for c in re.findall(r"RUT-\d{8}-\d+", r)] == codigos,
      f"lee las 5 rutas mas recientes, de la mas nueva a la mas antigua: {r[:90]}")
check(all(t in r for t in texto_primera), "con su fecha, estado, paradas y entregadas")
check(dice("conductor/mi-historial", C3) == "Aún no tienes rutas registradas.", "sin rutas lo dice")
r = dice("conductor/mi-historial", C)
check(codigo_ruta in r.split(";")[0] and "en curso" in r.split(";")[0].lower(),
      f"la ruta de hoy aparece primero, en curso: {r[:90]}")


print("\n== 6. Gestor: indicadores de la analitica ==")
for nombre in ("tiempo-promedio-entrega", "cumplimiento-ventana", "productividad-conductores", "causas-fallo"):
    estado, cuerpo = funcion(f"gestor/{nombre}", G)
    check(estado == 200 and "En los últimos 14 días" in cuerpo.get("mensaje", ""),
          f"el gestor consulta {nombre} con el periodo por defecto del panel")
    check(funcion(f"gestor/{nombre}", A)[0] == 200, f"el admin tambien consulta {nombre}")
    check(funcion(f"gestor/{nombre}", C)[0] == 403 and funcion(f"admin/{nombre}", A)[0] == 404,
          f"{nombre}: el conductor no la llama, y ya no esta en el espacio del admin")
check(funcion("admin/resumen-rendimiento", G)[0] == 403, "el panel de rendimiento sigue siendo solo del admin")


print("\n== 7. Gestor: atender-solicitud ==")
with app.app_context():
    portal_cliente = db.session.get(Cliente, parada2.cliente_id)
    cuenta_portal = db.session.query(Usuario).filter_by(correo="cliente@sgds.com").one()
    registrar_solicitud(portal_cliente, cuenta_portal.id, "Cambiar la hora de entrega")
    db.session.commit()
    nombre_cliente = portal_cliente.nombre
notificaciones.esperar_envios()
r = dice("gestor/solicitudes-contacto-pendientes", G)
check(nombre_cliente in r and r.endswith("Para marcar una como atendida, dime de qué cliente es."),
      "las pendientes ofrecen marcarlas como atendidas por voz")
check(dice("gestor/atender-solicitud", G) == "Me falta el cliente.", "sin cliente lo pide")
check(dice("gestor/atender-solicitud", G, {"cliente": "Ferreteria Inexistente"})
      == "No encontré el cliente Ferreteria Inexistente.", "un cliente desconocido se dice")
check(dice("gestor/atender-solicitud", G, {"cliente": "distribuidora norte"})
      == "Distribuidora Norte no tiene una solicitud de contacto pendiente.",
      "un cliente sin solicitud pendiente se dice sin pedir confirmacion")
envios_antes = len(envios)
r, final = confirma("gestor/atender-solicitud", G, {"cliente": "el portal"})
check(r.startswith(f"Voy a marcar como atendida la solicitud de {nombre_cliente} del ")
      and r.endswith("por Cambiar la hora de entrega. ¿confirmas?"), f"el resumen lee cliente, fecha y motivo: {r}")
check(final == f"Listo, la solicitud de contacto de {nombre_cliente} quedó atendida.", f"con confirmar la atiende: {final}")
with app.app_context():
    solicitud = db.session.query(SolicitudContacto).filter_by(cliente_id=parada2.cliente_id).one()
    check(solicitud.atendida and solicitud.atendida_por_id == gestor_id and solicitud.atendida_en is not None,
          "queda atendida por el gestor de la llamada, como en pantalla")
registro = auditoria("Atender solicitud")
check(registro is not None and registro.origen == "voz" and registro.entidad == "solicitud",
      "queda en la auditoria, por voz, sobre la solicitud")
check(len(eventos("accion_asistente", envios_antes)) == 1, "y envia el correo accion_asistente")
check(dice("gestor/atender-solicitud", G, {"cliente": "el portal"})
      == f"{nombre_cliente} no tiene una solicitud de contacto pendiente.", "atenderla dos veces no se puede")
check(funcion("gestor/atender-solicitud", C, {"cliente": "el portal"})[0] == 403, "el conductor no la llama")


print("\n== 8. Admin: bitacora de lo anterior ==")
r = dice("admin/bitacora", A, {"usuario": "andres molina"})
check(r.startswith("Hay ") and "de Andres Molina" in r and "por voz" in r and "recibida por Luis Pardo" in r,
      f"filtra por usuario y lee las acciones del conductor por voz: {r[:100]}")
r = dice("admin/bitacora", A, {"entidad": "solicitudes"})
check("Hay 1 registro sobre solicitudes" in r and "Carlos Rueda" in r, f"filtra por entidad: {r[:100]}")
r = dice("admin/bitacora", A, {"entidad": "ruta", "desde": hoy().isoformat(), "hasta": hoy().isoformat()})
check(f"Voy a iniciar tu ruta {codigo_ruta}" in r and "desde el" in r, f"filtra por fecha y entidad: {r[:100]}")
check(funcion("admin/bitacora", G)[0] == 403, "la bitacora es solo del admin")


print("\n" + "=" * 55)
print("RESULTADO: " + ("TODAS LAS PRUEBAS PASARON" if not fallos else f"{len(fallos)} FALLAS"))
for f in fallos: print("   - " + f)
sys.exit(1 if fallos else 0)
