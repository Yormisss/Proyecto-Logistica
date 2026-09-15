"""Introduce las tablas `clientes` y `direcciones_cliente`.

El proyecto crea el esquema con `db.create_all()`, que agrega tablas nuevas pero
nunca columnas nuevas en tablas existentes. Este cambio si toca `pedidos`, y una
base de piloto no se puede resolver borrandola, asi que la migracion:

  1. crea las tablas nuevas,
  2. agrega `cliente_id` y `direccion_id` a `pedidos` si faltan,
  3. crea el indice y las claves ajenas que un `ALTER TABLE ADD COLUMN` no genera,
  4. reconstruye los clientes y sus sedes a partir de los pedidos ya cargados,
     agrupando por nombre normalizado para que las variaciones de escritura
     acumuladas en el historico colapsen en un solo registro.

El paso 3 no es cosmetico: MySQL Workbench dibuja el diagrama EER a partir de las
claves ajenas declaradas, de modo que sin el la base migrada mostraria las tablas
de clientes sueltas, sin las lineas que las unen a `pedidos`. Ademas queda sin
integridad referencial en esas dos columnas. El resultado converge al mismo
esquema que produce `init-db` sobre una base vacia.

Es idempotente: volver a ejecutarla no duplica nada.

    flask --app run migrar-clientes
"""

from sqlalchemy import inspect, text

from app.extensions import db
from app.models import Cliente, DireccionCliente, Pedido, normalizar_texto

# Las columnas se agregan como nullable, que es lo unico que SQLite admite en un
# ALTER TABLE y lo que corresponde: los pedidos anteriores a la migracion se
# vinculan en el paso de backfill, no en el DDL.
COLUMNAS_PEDIDO = {
    "cliente_id": "INTEGER",
    "direccion_id": "INTEGER",
}


def _columnas(tabla):
    inspector = inspect(db.engine)
    if tabla not in inspector.get_table_names():
        return set()
    return {c["name"] for c in inspector.get_columns(tabla)}


def crear_tablas():
    """Crea `clientes` y `direcciones_cliente` sin tocar las demas."""
    db.metadata.create_all(
        bind=db.engine,
        tables=[Cliente.__table__, DireccionCliente.__table__],
        checkfirst=True,
    )
    return ["clientes", "direcciones_cliente"]


# Nombre del indice que declara el modelo (`cliente_id` con index=True). Se
# reproduce igual para que una base migrada y una creada desde cero no difieran.
INDICE_CLIENTE = "ix_pedidos_cliente_id"

CLAVES_AJENAS = (
    ("cliente_id", "clientes"),
    ("direccion_id", "direcciones_cliente"),
)


def agregar_columnas():
    """Agrega las claves ajenas a `pedidos`. Devuelve las que realmente creo."""
    existentes = _columnas("pedidos")
    agregadas = []
    for columna, tipo in COLUMNAS_PEDIDO.items():
        if columna in existentes:
            continue
        with db.engine.begin() as conexion:
            conexion.execute(text(f"ALTER TABLE pedidos ADD COLUMN {columna} {tipo}"))
        agregadas.append(columna)
    return agregadas


def agregar_indices_y_claves():
    """Crea el indice y las claves ajenas de las columnas recien agregadas.

    `ALTER TABLE ADD COLUMN` agrega la columna y nada mas: ni el indice que
    declara el modelo ni la clave ajena. Devuelve la lista de lo que creo.
    """
    inspector = inspect(db.engine)
    creados = []

    indices = {i["name"] for i in inspector.get_indexes("pedidos")}
    if INDICE_CLIENTE not in indices:
        with db.engine.begin() as conexion:
            conexion.execute(
                text(f"CREATE INDEX {INDICE_CLIENTE} ON pedidos (cliente_id)")
            )
        creados.append(INDICE_CLIENTE)

    # SQLite no admite agregar una clave ajena a una tabla existente (exigiria
    # reconstruirla) y tampoco las verifica salvo que se active por conexion, asi
    # que alli se omite sin perdida: el motor del piloto es MySQL.
    if db.engine.dialect.name != "mysql":
        return creados

    declaradas = {
        columna
        for fk in inspector.get_foreign_keys("pedidos")
        for columna in fk["constrained_columns"]
    }
    for columna, tabla in CLAVES_AJENAS:
        if columna in declaradas:
            continue
        with db.engine.begin() as conexion:
            conexion.execute(text(
                f"ALTER TABLE pedidos ADD CONSTRAINT fk_pedidos_{columna} "
                f"FOREIGN KEY ({columna}) REFERENCES {tabla} (id)"
            ))
        creados.append(f"fk_pedidos_{columna}")

    return creados


def reconstruir_clientes():
    """Deriva clientes y sedes de los pedidos que aun no estan vinculados.

    Solo lee pedidos con `cliente_id` nulo, de modo que una segunda ejecucion no
    encuentra nada por hacer.
    """
    pendientes = (
        db.session.query(Pedido)
        .filter(Pedido.cliente_id.is_(None))
        .order_by(Pedido.id)
        .all()
    )
    if not pendientes:
        return {"pedidos": 0, "clientes": 0, "sedes": 0}

    # Indice de lo que ya exista en la base, para no duplicar al re-ejecutar.
    clientes = {
        c.nombre_normalizado: c for c in db.session.query(Cliente).all()
    }
    sedes = {
        (d.cliente_id, d.direccion_normalizada, normalizar_texto(d.ciudad)): d
        for d in db.session.query(DireccionCliente).all()
    }

    nuevos_clientes = nuevas_sedes = 0

    for pedido in pendientes:
        clave_cliente = normalizar_texto(pedido.cliente_nombre)
        if not clave_cliente:
            continue

        cliente = clientes.get(clave_cliente)
        if cliente is None:
            cliente = Cliente(
                nombre=pedido.cliente_nombre.strip(),
                telefono=pedido.cliente_telefono,
            )
            db.session.add(cliente)
            db.session.flush()
            clientes[clave_cliente] = cliente
            nuevos_clientes += 1
        elif pedido.cliente_telefono and not cliente.telefono:
            cliente.telefono = pedido.cliente_telefono

        ciudad = (pedido.ciudad or "Bogota").strip()
        clave_sede = (cliente.id, normalizar_texto(pedido.direccion), normalizar_texto(ciudad))

        sede = sedes.get(clave_sede)
        if sede is None:
            propias = sum(1 for k in sedes if k[0] == cliente.id)
            sede = DireccionCliente(
                cliente_id=cliente.id,
                etiqueta="Principal" if not propias else f"Sede {propias + 1}",
                direccion=pedido.direccion,
                ciudad=ciudad,
                latitud=pedido.latitud,
                longitud=pedido.longitud,
                ventana_inicio=pedido.ventana_inicio,
                ventana_fin=pedido.ventana_fin,
            )
            db.session.add(sede)
            db.session.flush()
            sedes[clave_sede] = sede
            nuevas_sedes += 1
        elif sede.latitud is None and pedido.latitud is not None:
            sede.latitud = pedido.latitud
            sede.longitud = pedido.longitud

        # Solo se escriben las claves ajenas: los campos de entrega del pedido
        # son el snapshot historico y se dejan exactamente como estaban.
        pedido.cliente_id = cliente.id
        pedido.direccion_id = sede.id

    db.session.commit()
    return {
        "pedidos": len(pendientes),
        "clientes": nuevos_clientes,
        "sedes": nuevas_sedes,
    }


def aplicar(verboso=True):
    """Ejecuta la migracion completa y devuelve un resumen de lo aplicado."""
    tablas = crear_tablas()
    columnas = agregar_columnas()
    resumen = reconstruir_clientes()
    # Las claves ajenas van despues del backfill: MySQL valida las filas
    # existentes al declarar la restriccion, y antes del backfill `cliente_id`
    # esta nulo en todas (aceptable) pero cualquier valor huerfano abortaria.
    restricciones = agregar_indices_y_claves()

    if verboso:
        print(f"Tablas verificadas: {', '.join(tablas)}")
        print(
            "Columnas agregadas a pedidos: "
            + (", ".join(columnas) if columnas else "ninguna (ya existian)")
        )
        print(
            f"Backfill: {resumen['pedidos']} pedido(s) vinculados, "
            f"{resumen['clientes']} cliente(s) y {resumen['sedes']} sede(s) creadas."
        )
        print(
            "Indices y claves ajenas: "
            + (", ".join(restricciones) if restricciones else "ninguna (ya existian)")
        )
        if not resumen["pedidos"]:
            print("No habia pedidos sin vincular: la base ya estaba migrada.")

    return {
        "tablas": tablas, "columnas": columnas,
        "restricciones": restricciones, **resumen,
    }
