"""Normalizacion de clientes y portal de seguimiento del destinatario.

Verifica las tres cosas que introduce el cambio de esquema: que los clientes
dejen de repetirse por pedido, que el snapshot historico de la entrega siga
intacto, y que el portal solo muestre las ordenes propias sin filtrar la ruta
—que agrupa paradas de varios clientes— ni los datos de terceros.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from run import app
from app.extensions import db
from app.models import (
    Cliente, DireccionCliente, EstadoPedido, Pedido, Rol, Usuario, normalizar_texto,
)

fallos = []
def check(cond, msg):
    print(("  OK   " if cond else "  FALLA") + f" {msg}")
    if not cond: fallos.append(msg)

app.config["WTF_CSRF_ENABLED"] = False

NIT_PORTAL = "900123456-1"      # Supermercado El Portal (tiene cuenta)
NIT_AJENO = "900345678-3"       # Minimercado Chapinero (no tiene cuenta)


def sesion(correo, clave):
    c = app.test_client()
    c.post("/auth/login", data={"correo": correo, "contrasena": clave})
    return c


def cliente_portal():
    """Recarga el cliente del portal en el contexto activo.

    Un objeto obtenido en un `app_context` anterior queda desprendido de su
    sesion y sus relaciones responden vacio, asi que cada bloque lo consulta
    de nuevo en vez de reutilizar la instancia.
    """
    return db.session.query(Cliente).filter_by(documento=NIT_PORTAL).one()


print("\n== 1. Normalizacion del esquema ==")
with app.app_context():
    total_clientes = db.session.query(Cliente).count()
    total_pedidos = db.session.query(Pedido).count()
    check(total_clientes > 0, f"la tabla clientes se poblo ({total_clientes} registros)")
    check(
        total_clientes < total_pedidos,
        f"{total_clientes} clientes para {total_pedidos} pedidos (antes se repetian por pedido)",
    )
    huerfanos = db.session.query(Pedido).filter(Pedido.cliente_id.is_(None)).count()
    check(huerfanos == 0, f"ningun pedido quedo sin cliente ({huerfanos} huerfanos)")
    sin_sede = db.session.query(Pedido).filter(Pedido.direccion_id.is_(None)).count()
    check(sin_sede == 0, f"ningun pedido quedo sin sede ({sin_sede} huerfanos)")

    # Un nombre normalizado no puede corresponder a dos clientes.
    claves = [c.nombre_normalizado for c in db.session.query(Cliente).all()]
    check(len(claves) == len(set(claves)), "no hay clientes duplicados por nombre normalizado")

    check(
        normalizar_texto("  Supermercado  El Portál. ") == normalizar_texto("SUPERMERCADO EL PORTAL"),
        "la clave de deduplicacion ignora acentos, mayusculas y puntuacion",
    )

print("\n== 2. Un cliente sostiene varias sedes ==")
with app.app_context():
    portal = cliente_portal()
    ID_PORTAL, NOMBRE_PORTAL = portal.id, portal.nombre
    check(len(portal.direcciones) >= 2, f"{portal.nombre} tiene {len(portal.direcciones)} sedes")
    etiquetas = {d.etiqueta for d in portal.direcciones}
    check(len(etiquetas) == len(portal.direcciones), "cada sede tiene su propia etiqueta")
    check(
        all(d.cliente_id == portal.id for d in portal.direcciones),
        "las sedes pertenecen al cliente correcto",
    )

print("\n== 3. El snapshot historico de la entrega se conserva ==")
with app.app_context():
    pedido = (
        db.session.query(Pedido)
        .filter(Pedido.cliente_id == ID_PORTAL, Pedido.direccion.isnot(None))
        .first()
    )
    check(bool(pedido.cliente_nombre), "el pedido conserva el nombre del destinatario")
    check(bool(pedido.direccion), "el pedido conserva la direccion despachada")

    # Mover la sede no puede reescribir las entregas ya registradas.
    sede = pedido.direccion_entrega
    direccion_original = pedido.direccion
    sede.direccion = "Calle Nueva #1-99 (traslado)"
    db.session.flush()
    db.session.refresh(pedido)
    check(
        pedido.direccion == direccion_original,
        "trasladar la sede no altera la direccion del pedido ya despachado",
    )
    check(
        pedido.direccion_entrega.direccion != pedido.direccion,
        "la sede apunta al dato nuevo y el pedido al historico",
    )
    db.session.rollback()

print("\n== 4. Aterrizaje por rol (el cliente no cae en el tablero admin) ==")
r = app.test_client().post(
    "/auth/login", data={"correo": "cliente@sgds.com", "contrasena": "Cliente123*"},
    follow_redirects=True,
)
check(r.status_code == 200, "el cliente inicia sesion")
check(b"Mis pedidos" in r.data, "aterriza en su portal, no en el tablero administrativo")
check(b"Tablero de control" not in r.data, "no se le muestra el tablero interno")

cliente_c = sesion("cliente@sgds.com", "Cliente123*")
r = cliente_c.get("/", follow_redirects=True)
check(b"Mis pedidos" in r.data, "la raiz tambien lo envia a su portal")

print("\n== 5. El portal solo expone las ordenes propias (RNF5) ==")
with app.app_context():
    mio = db.session.query(Pedido).filter(Pedido.cliente_id == ID_PORTAL).first().id
    ajeno_cliente = db.session.query(Cliente).filter_by(documento=NIT_AJENO).one()
    ajeno = db.session.query(Pedido).filter(Pedido.cliente_id == ajeno_cliente.id).first().id
    nombre_ajeno = ajeno_cliente.nombre

check(cliente_c.get(f"/portal/pedido/{mio}").status_code == 200, "puede ver su propio pedido")
check(
    cliente_c.get(f"/portal/pedido/{ajeno}").status_code == 403,
    "cambiar el id de la URL no revela el pedido de otro cliente",
)
check(cliente_c.get("/portal/pedido/999999").status_code == 404, "un pedido inexistente da 404")

listado = cliente_c.get("/portal/").data.decode()
check(NOMBRE_PORTAL in listado, "el listado identifica al cliente de la sesion")
check(nombre_ajeno not in listado, f"el listado no menciona a otro cliente ({nombre_ajeno})")

print("\n== 6. El portal no filtra datos de la ruta ni de terceros ==")
detalle = cliente_c.get(f"/portal/pedido/{mio}").data.decode()
check("RUT-" not in detalle, "no expone el codigo de la ruta")
with app.app_context():
    conductores = [
        u.nombre for u in db.session.query(Usuario).filter_by(rol=Rol.CONDUCTOR).all()
    ]
check(
    all(nombre not in detalle for nombre in conductores),
    "no expone el nombre del conductor asignado",
)
check("polyline" not in detalle and "geometria" not in detalle, "no expone la geometria del recorrido")
check(
    "Creado por importacion" not in detalle and "Carga inicial" not in detalle,
    "la bitacora se traduce y omite las notas internas",
)

print("\n== 7. Segregacion de funciones entre roles ==")
for url in ("/admin/", "/pedidos/", "/rutas/", "/inventario/", "/conductor/"):
    check(cliente_c.get(url).status_code == 403, f"el cliente no accede a {url}")

for correo, clave, rol in (
    ("admin@sgds.com", "Admin123*", "admin"),
    ("despachador@sgds.com", "Despacho123*", "despachador"),
    ("conductor1@sgds.com", "Conductor123*", "conductor"),
):
    check(sesion(correo, clave).get("/portal/").status_code == 403,
          f"el {rol} no accede al portal del cliente")

print("\n== 8. Una cuenta CLIENTE sin cliente asociado no ve nada ==")
with app.app_context():
    huerfano = Usuario(nombre="Cuenta sin vincular", correo="huerfano@sgds.com",
                       rol=Rol.CLIENTE, activo=True)
    huerfano.establecer_contrasena("Huerfano123*")
    db.session.add(huerfano)
    db.session.commit()

sin_vinculo = sesion("huerfano@sgds.com", "Huerfano123*")
check(sin_vinculo.get("/portal/").status_code == 403,
      "una cuenta a medio configurar no consulta la operacion completa")

print("\n== 9. Filtros y sedes del portal ==")
check(cliente_c.get("/portal/?estado=abiertos").status_code == 200, "filtro de pedidos en curso")
check(cliente_c.get("/portal/?estado=cerrados").status_code == 200, "filtro de pedidos cerrados")
check(cliente_c.get("/portal/?estado=inventado").status_code == 200, "un filtro invalido no rompe la vista")
r = cliente_c.get("/portal/direcciones")
check(r.status_code == 200, "consulta sus sedes registradas")
check(b"Principal" in r.data, "lista la sede principal")

print("\n== 10. La importacion no duplica clientes existentes ==")
CSV = (
    "cliente_nombre,cliente_documento,direccion,ciudad,latitud,longitud,sku,cantidad\n"
    # Tres escrituras del mismo cliente ya registrado.
    "Supermercado El Portal,,Av. Cra 68 #75-50,Bogota,4.6795,-74.0895,SKU-1001,3\n"
    "SUPERMERCADO  EL PORTAL,,av. cra 68 #75-50,bogota,,,SKU-1002,2\n"
    "Supermercado El Portál.,,Av. Cra 68 #75-50,Bogota,,,SKU-1003,1\n"
    # Mismo NIT, razon social distinta: debe reconocerse por el documento.
    f"La Esquina S.A.S.,900234567-2,Calle 63 #24-18,Bogota,4.6483,-74.0715,SKU-1001,4\n"
    # Cliente genuinamente nuevo.
    "Panaderia Nueva Era,901111111-9,Calle 45 #12-30,Bogota,4.6300,-74.0700,SKU-1001,7\n"
)

with app.app_context():
    from app.services.importador import analizar_csv, guardar_pedidos

    antes_clientes = db.session.query(Cliente).count()
    antes_sedes = db.session.query(DireccionCliente).count()
    antes_pedidos = db.session.query(Pedido).count()

    pedidos_csv, errores = analizar_csv(CSV.encode())
    check(not errores, f"el archivo se analiza sin errores ({errores})")
    check(len(pedidos_csv) == 5, f"se reconocen 5 pedidos (encontrados: {len(pedidos_csv)})")

    nuevos = [p for p in pedidos_csv if not p["cliente_existente"]]
    check(len(nuevos) == 1, f"la vista previa marca 1 cliente nuevo (marcados: {len(nuevos)})")
    check(
        nuevos and nuevos[0]["cliente_nombre"] == "Panaderia Nueva Era",
        "el cliente marcado como nuevo es el correcto",
    )

    despachador = db.session.query(Usuario).filter_by(correo="despachador@sgds.com").one()
    guardar_pedidos(pedidos_csv, despachador.id)

    creados_clientes = db.session.query(Cliente).count() - antes_clientes
    creados_sedes = db.session.query(DireccionCliente).count() - antes_sedes
    creados_pedidos = db.session.query(Pedido).count() - antes_pedidos

    check(creados_pedidos == 5, f"se cargaron los 5 pedidos ({creados_pedidos})")
    check(creados_clientes == 1, f"solo se creo 1 cliente nuevo ({creados_clientes})")
    check(creados_sedes == 1, f"solo se creo 1 sede nueva ({creados_sedes})")

    # Las tres variantes deben apuntar al mismo cliente.
    variantes = (
        db.session.query(Pedido.cliente_id)
        .filter(Pedido.cliente_nombre.ilike("%Portal%"))
        .distinct()
        .all()
    )
    check(len(variantes) == 1, f"las variantes de escritura comparten cliente ({len(variantes)} distintos)")

    por_nit = db.session.query(Cliente).filter_by(documento="900234567-2").one()
    check(
        por_nit.nombre == "Tienda La Esquina",
        "el NIT reconocio al cliente sin sobrescribir su razon social registrada",
    )

print("\n== 11. Consulta exacta de los pedidos de un cliente ==")
with app.app_context():
    portal = cliente_portal()
    # El punto del cambio: contar por clave ajena, no por coincidencia de texto.
    por_clave = db.session.query(Pedido).filter(Pedido.cliente_id == portal.id).count()
    por_texto = (
        db.session.query(Pedido)
        .filter(Pedido.cliente_nombre == portal.nombre)
        .count()
    )
    check(por_clave > 0, f"el cliente tiene {por_clave} pedidos por clave ajena")
    check(
        por_clave >= por_texto,
        f"la clave ajena no pierde pedidos frente a la busqueda por texto ({por_clave} vs {por_texto})",
    )
    check(portal.pedidos.count() == por_clave, "la relacion del modelo devuelve lo mismo que la consulta")

print("\n== 12. Migracion de una base con datos previos ==")
with app.app_context():
    from migraciones.m001_clientes_y_direcciones import aplicar

    # Se desvincula un pedido para simular una fila anterior a la migracion.
    suelto = db.session.query(Pedido).filter(Pedido.cliente_id.isnot(None)).first()
    id_suelto, cliente_previo = suelto.id, suelto.cliente_id
    suelto.cliente_id = None
    suelto.direccion_id = None
    db.session.commit()

    resumen = aplicar(verboso=False)
    check(resumen["pedidos"] == 1, f"la migracion detecta el pedido sin vincular ({resumen['pedidos']})")
    check(resumen["clientes"] == 0, "no crea un cliente duplicado para un destinatario ya registrado")

    revinculado = db.session.get(Pedido, id_suelto)
    check(revinculado.cliente_id == cliente_previo, "lo reasigna al cliente correcto")

    # Idempotencia: una segunda pasada no debe encontrar nada.
    repetida = aplicar(verboso=False)
    check(repetida["pedidos"] == 0, "una segunda ejecucion no vuelve a tocar nada")
    check(repetida["clientes"] == 0 and repetida["sedes"] == 0, "la migracion es idempotente")
    check(repetida["restricciones"] == [], "no vuelve a declarar indices ni claves ajenas")

print("\n== 13. El esquema declara las claves ajenas (diagrama EER de Workbench) ==")
with app.app_context():
    from sqlalchemy import inspect

    inspector = inspect(db.engine)
    motor = db.engine.dialect.name

    indices = {i["name"] for i in inspector.get_indexes("pedidos")}
    check("ix_pedidos_cliente_id" in indices, "pedidos.cliente_id esta indexado")

    if motor == "mysql":
        # Workbench dibuja las relaciones del diagrama EER a partir de las claves
        # ajenas: sin declararlas, las tablas de clientes apareceran sueltas.
        referencias = {
            columna: fk["referred_table"]
            for fk in inspector.get_foreign_keys("pedidos")
            for columna in fk["constrained_columns"]
        }
        check(referencias.get("cliente_id") == "clientes",
              "pedidos.cliente_id declara la clave ajena a clientes")
        check(referencias.get("direccion_id") == "direcciones_cliente",
              "pedidos.direccion_id declara la clave ajena a direcciones_cliente")

        sedes_fk = {
            fk["referred_table"] for fk in inspector.get_foreign_keys("direcciones_cliente")
        }
        check("clientes" in sedes_fk, "direcciones_cliente apunta a clientes")

        clientes_fk = {fk["referred_table"] for fk in inspector.get_foreign_keys("clientes")}
        check("usuarios" in clientes_fk, "clientes.usuario_id apunta a usuarios")

        unicos = {
            i["name"] for i in inspector.get_indexes("clientes") if i["unique"]
        }
        check("ix_clientes_usuario_id" in unicos,
              "clientes.usuario_id es unico (relacion 1 a 0..1)")
    else:
        # SQLite no admite agregar claves ajenas a una tabla existente ni las
        # verifica por defecto; el motor del piloto es MySQL.
        print(f"       (motor {motor}: verificacion de claves ajenas omitida)")

print("\n" + "=" * 55)
print("RESULTADO: " + ("TODAS LAS PRUEBAS PASARON" if not fallos else f"{len(fallos)} FALLAS"))
for f in fallos:
    print("   - " + f)
sys.exit(1 if fallos else 0)
