"""Carga de datos de demostracion para el entorno de pruebas del prototipo.

Genera el escenario descrito en el documento: un centro de distribucion en Bogota,
una flota reducida y pedidos de ultima milla con ventanas horarias comerciales
(numeral 1.4 - Fase de implementacion piloto).
"""

from datetime import datetime, time, timedelta

from app.extensions import db
from app.models import (
    Cliente,
    MovimientoInventario,
    PruebaEntrega,
    TipoMovimiento,
    EstadoPedido,
    EstadoRuta,
    EventoPedido,
    Pedido,
    PedidoItem,
    Producto,
    Rol,
    Ruta,
    Usuario,
    Vehiculo,
)
from app.services.clientes import Resolutor, vincular_destino
from app.tiempo import hoy as fecha_actual

USUARIOS = [
    ("Laura Gomez", "admin@sgds.com", Rol.ADMIN, "Admin123*"),
    ("Carlos Rueda", "despachador@sgds.com", Rol.DESPACHADOR, "Despacho123*"),
    ("Andres Molina", "conductor1@sgds.com", Rol.CONDUCTOR, "Conductor123*"),
    ("Diego Pardo", "conductor2@sgds.com", Rol.CONDUCTOR, "Conductor123*"),
    # Cuenta del portal de seguimiento. Se vincula mas abajo al primer cliente
    # de DESTINOS, que es el que acumula mas historico.
    ("Marcela Rios", "cliente@sgds.com", Rol.CLIENTE, "Cliente123*"),
]

PRODUCTOS = [
    ("SKU-1001", "Caja bebidas 12 und", 240, 60),
    ("SKU-1002", "Paquete snacks 24 und", 180, 50),
    ("SKU-1003", "Bolsa arroz 5 kg", 95, 40),
    ("SKU-1004", "Aceite vegetal 1 L", 30, 45),   # Bajo minimo: alimenta la alerta
    ("SKU-1005", "Detergente 2 kg", 120, 30),
    ("SKU-1006", "Papel higienico 12 rollos", 18, 25),  # Bajo minimo
]

# Clientes comerciales con su sede de entrega. El ultimo campo es el NIT, clave
# natural que evita duplicarlos cuando el ERP reescribe la razon social.
DESTINOS = [
    ("Supermercado El Portal", "Av. Cra 68 #75-50", 4.6795, -74.0895, time(8, 0), time(11, 0), "900123456-1"),
    ("Tienda La Esquina", "Calle 63 #24-18", 4.6483, -74.0715, time(8, 0), time(12, 0), "900234567-2"),
    ("Minimercado Chapinero", "Cra 13 #53-40", 4.6412, -74.0637, time(9, 0), time(13, 0), "900345678-3"),
    ("Distribuidora Norte", "Av. Suba #116-25", 4.7014, -74.0713, time(10, 0), time(14, 0), "900456789-4"),
    ("Autoservicio Kennedy", "Av. 1 de Mayo #40-30", 4.6285, -74.1465, time(13, 0), time(17, 0), "900567890-5"),
    ("Tienda Fontibon", "Cra 100 #17-20", 4.6712, -74.1445, time(14, 0), time(18, 0), "900678901-6"),
]

# Segunda sede del primer cliente: demuestra que un cliente sostiene varios
# puntos de entrega sin duplicar su registro.
SEDE_ADICIONAL = ("Supermercado El Portal", "Sede Toberin", "Av. Cra 19 #166-30",
                  4.7398, -74.0301, time(9, 0), time(12, 0))


def sembrar_datos():
    db.create_all()

    if db.session.query(Usuario).count():
        print("La base ya contiene datos. Ejecute 'flask --app run reset-db' para limpiarla.")
        return

    # --- Usuarios (RF1) ---
    usuarios = {}
    for nombre, correo, rol, clave in USUARIOS:
        usuario = Usuario(nombre=nombre, correo=correo, rol=rol, activo=True)
        usuario.establecer_contrasena(clave)
        db.session.add(usuario)
        usuarios[correo] = usuario
    db.session.flush()

    # --- Flota ---
    vehiculos = [
        Vehiculo(placa="WGT-482", tipo="Furgon", capacidad_kg=1200, capacidad_unidades=400,
                 conductor_id=usuarios["conductor1@sgds.com"].id),
        Vehiculo(placa="KLM-317", tipo="Camioneta", capacidad_kg=800, capacidad_unidades=250,
                 conductor_id=usuarios["conductor2@sgds.com"].id),
    ]
    db.session.add_all(vehiculos)

    # --- Clientes y sedes de entrega ---
    # Se crean antes que los pedidos para que cada orden nazca ya asociada a su
    # cliente, en vez de depender del nombre escrito en el pedido.
    resolutor = Resolutor()
    for nombre, direccion, lat, lng, inicio, fin, nit in DESTINOS:
        cliente = resolutor.cliente(nombre, documento=nit)
        resolutor.direccion(cliente, direccion, ciudad="Bogota", latitud=lat,
                            longitud=lng, ventana_inicio=inicio, ventana_fin=fin)

    nombre_sede, etiqueta, direccion, lat, lng, inicio, fin = SEDE_ADICIONAL
    sede_extra = resolutor.direccion(
        resolutor.cliente(nombre_sede), direccion, ciudad="Bogota",
        latitud=lat, longitud=lng, ventana_inicio=inicio, ventana_fin=fin,
    )
    sede_extra.etiqueta = etiqueta

    # Cuenta del portal: se vincula al primer cliente (relacion 1 a 0..1).
    resolutor.cliente(DESTINOS[0][0]).usuario_id = usuarios["cliente@sgds.com"].id
    db.session.flush()

    # --- Inventario ---
    productos = []
    for sku, nombre, stock, minimo in PRODUCTOS:
        producto = Producto(sku=sku, nombre=nombre, stock_actual=stock, stock_minimo=minimo)
        productos.append(producto)
        db.session.add(producto)
    db.session.flush()

    # --- Pedidos del dia y ruta asignada ---
    hoy = fecha_actual()
    ruta = Ruta(
        codigo=f"RUT-{hoy.strftime('%Y%m%d')}-01",
        fecha=hoy,
        estado=EstadoRuta.EN_CURSO,
        conductor_id=usuarios["conductor1@sgds.com"].id,
        vehiculo_id=vehiculos[0].id,
        distancia_km=42.6,
        duracion_min=118,
        proveedor_ruteo="OSRM",
    )
    db.session.add(ruta)
    db.session.flush()

    # Estados variados para que el tablero (RF6) muestre KPIs con datos reales.
    plan = [
        (EstadoPedido.ENTREGADO, ruta, 1),
        (EstadoPedido.ENTREGADO, ruta, 2),
        (EstadoPedido.EN_RUTA, ruta, 3),
        (EstadoPedido.ASIGNADO, ruta, 4),
        (EstadoPedido.FALLIDO, ruta, 5),
        (EstadoPedido.PENDIENTE, None, None),
    ]

    for indice, (destino, (estado, ruta_asignada, orden)) in enumerate(zip(DESTINOS, plan), start=1):
        nombre, direccion, lat, lng, inicio, fin, nit = destino
        cliente = resolutor.cliente(nombre, documento=nit,
                                    telefono=f"31{indice}5550{indice}{indice}")
        sede = resolutor.direccion(cliente, direccion, ciudad="Bogota",
                                   latitud=lat, longitud=lng)

        pedido = Pedido(
            codigo=f"PED-{hoy.strftime('%Y%m%d')}-{indice:03d}",
            cliente_nombre=nombre,
            cliente_telefono=f"31{indice}5550{indice}{indice}",
            direccion=direccion,
            latitud=lat,
            longitud=lng,
            ventana_inicio=inicio,
            ventana_fin=fin,
            estado=estado,
            fecha_despacho=hoy,
            prioridad=2 if indice <= 3 else 3,
            ruta_id=ruta_asignada.id if ruta_asignada else None,
            orden_en_ruta=orden,
            creado_por_id=usuarios["despachador@sgds.com"].id,
            inventario_descontado=(estado == EstadoPedido.ENTREGADO),
        )
        vincular_destino(pedido, cliente, sede)
        db.session.add(pedido)
        db.session.flush()

        db.session.add(PedidoItem(pedido_id=pedido.id, producto_id=productos[indice % len(productos)].id,
                                  cantidad=2 + indice))
        db.session.add(EventoPedido(pedido_id=pedido.id, estado_nuevo=estado,
                                    usuario_id=usuarios["despachador@sgds.com"].id,
                                    nota="Carga inicial de demostracion"))

    # Pedido de ayer, para verificar el filtro por fecha del tablero.
    ayer = hoy - timedelta(days=1)
    cliente_ayer = resolutor.cliente("Tienda Usaquen", documento="900789012-7")
    sede_ayer = resolutor.direccion(cliente_ayer, "Cra 7 #117-20", ciudad="Bogota",
                                    latitud=4.7031, longitud=-74.0308)
    pedido_ayer = Pedido(
        codigo=f"PED-{ayer.strftime('%Y%m%d')}-001",
        cliente_nombre="Tienda Usaquen",
        direccion="Cra 7 #117-20",
        latitud=4.7031, longitud=-74.0308,
        estado=EstadoPedido.ENTREGADO,
        fecha_despacho=ayer,
        inventario_descontado=True,
        creado_por_id=usuarios["despachador@sgds.com"].id,
    )
    vincular_destino(pedido_ayer, cliente_ayer, sede_ayer)
    db.session.add(pedido_ayer)

    db.session.commit()

    # Traza la ruta de demostracion con el proveedor de geolocalizacion (RF3),
    # para que el mapa del tablero tenga contenido real desde el primer arranque.
    _trazar_ruta_demo(ruta)

    # Historico de operacion, necesario para que la analitica tenga series.
    _generar_historico(usuarios, productos, resolutor, dias=21)

    print("Datos de demostracion cargados.")
    print("-" * 58)
    for nombre, correo, rol, clave in USUARIOS:
        print(f"  {Rol.ETIQUETAS[rol]:<18} {correo:<26} {clave}")
    print("-" * 58)


MOTIVOS_DEMO = [
    ("Cliente ausente", 4),
    ("Establecimiento cerrado", 3),
    ("Direccion incorrecta", 2),
    ("Fuera de la ventana horaria", 2),
    ("Zona de dificil acceso", 1),
]


def _generar_historico(usuarios, productos, resolutor, dias=21):
    """Crea operacion pasada para alimentar los indicadores del tablero.

    Usa una semilla fija: el escenario es identico en cada siembra, de modo que
    las cifras mostradas en la sustentacion sean reproducibles.
    """
    import random

    aleatorio = random.Random(2026)
    hoy = fecha_actual()
    conductores = [
        usuarios["conductor1@sgds.com"],
        usuarios["conductor2@sgds.com"],
    ]
    despachador = usuarios["despachador@sgds.com"]

    bolsa_motivos = []
    for motivo, peso in MOTIVOS_DEMO:
        bolsa_motivos.extend([motivo] * peso)

    consecutivo = 0

    for desplazamiento in range(dias, 0, -1):
        dia = hoy - timedelta(days=desplazamiento)
        if dia.weekday() == 6:      # Domingo sin operacion
            continue

        for indice, conductor in enumerate(conductores, start=1):
            paradas = aleatorio.randint(4, 8)

            ruta = Ruta(
                codigo=f"RUT-{dia.strftime('%Y%m%d')}-{indice:02d}",
                fecha=dia,
                estado=EstadoRuta.FINALIZADA,
                conductor_id=conductor.id,
                distancia_km=round(aleatorio.uniform(28, 62), 1),
                duracion_min=round(aleatorio.uniform(90, 190), 0),
                proveedor_ruteo="OSRM",
                creada_en=datetime.combine(dia, time(6, 30)),
                iniciada_en=datetime.combine(dia, time(7, 30)),
                finalizada_en=datetime.combine(dia, time(16, 0)),
            )
            db.session.add(ruta)
            db.session.flush()

            salida = datetime.combine(dia, time(7, 30))

            for parada in range(1, paradas + 1):
                consecutivo += 1
                fallida = aleatorio.random() < 0.14      # ~14% de fallos
                estado = EstadoPedido.FALLIDO if fallida else EstadoPedido.ENTREGADO

                ventana_fin = time(aleatorio.choice([11, 12, 13, 15, 17]), 0)
                destino = DESTINOS[(consecutivo - 1) % len(DESTINOS)]

                cliente = resolutor.cliente(destino[0], documento=destino[6])
                sede = resolutor.direccion(cliente, destino[1], ciudad="Bogota",
                                          latitud=destino[2], longitud=destino[3])

                pedido = Pedido(
                    codigo=f"PED-{dia.strftime('%Y%m%d')}-{consecutivo:03d}",
                    cliente_nombre=destino[0],
                    direccion=destino[1],
                    latitud=destino[2],
                    longitud=destino[3],
                    ventana_inicio=time(8, 0),
                    ventana_fin=ventana_fin,
                    estado=estado,
                    fecha_despacho=dia,
                    prioridad=aleatorio.choice([2, 3, 3]),
                    ruta_id=ruta.id,
                    orden_en_ruta=parada,
                    creado_por_id=despachador.id,
                    inventario_descontado=not fallida,
                    creado_en=datetime.combine(dia, time(6, 0)),
                )
                vincular_destino(pedido, cliente, sede)
                db.session.add(pedido)
                db.session.flush()

                producto = productos[consecutivo % len(productos)]
                cantidad = aleatorio.randint(2, 9)
                db.session.add(
                    PedidoItem(pedido_id=pedido.id, producto_id=producto.id, cantidad=cantidad)
                )

                # Bitacora con tiempos realistas: alimenta el indicador de
                # duracion promedio entre "en ruta" y "entregado".
                salida += timedelta(minutes=aleatorio.randint(12, 38))
                llegada = salida + timedelta(minutes=aleatorio.randint(9, 34))

                db.session.add(EventoPedido(
                    pedido_id=pedido.id, usuario_id=despachador.id,
                    estado_anterior=EstadoPedido.PENDIENTE,
                    estado_nuevo=EstadoPedido.ASIGNADO,
                    registrado_en=datetime.combine(dia, time(6, 45)),
                ))
                db.session.add(EventoPedido(
                    pedido_id=pedido.id, usuario_id=conductor.id,
                    estado_anterior=EstadoPedido.ASIGNADO,
                    estado_nuevo=EstadoPedido.EN_RUTA,
                    registrado_en=salida,
                ))
                db.session.add(EventoPedido(
                    pedido_id=pedido.id, usuario_id=conductor.id,
                    estado_anterior=EstadoPedido.EN_RUTA,
                    estado_nuevo=estado,
                    registrado_en=llegada,
                ))

                db.session.add(PruebaEntrega(
                    pedido_id=pedido.id,
                    receptor_nombre=None if fallida else f"Receptor {consecutivo}",
                    motivo_fallo=aleatorio.choice(bolsa_motivos) if fallida else None,
                    registrado_en=llegada,
                ))

                if not fallida:
                    producto.stock_actual = max(producto.stock_actual - cantidad, 0)
                    db.session.add(MovimientoInventario(
                        producto_id=producto.id, pedido_id=pedido.id,
                        usuario_id=conductor.id, tipo=TipoMovimiento.SALIDA,
                        cantidad=cantidad, stock_resultante=producto.stock_actual,
                        motivo=f"Entrega confirmada del pedido {pedido.codigo}",
                        registrado_en=llegada,
                    ))

                salida = llegada

    db.session.commit()
    total = db.session.query(Pedido).filter(Pedido.fecha_despacho < hoy).count()
    print(f"Historico generado: {total} pedidos en {dias} dias previos.")
    print(f"Clientes normalizados: {db.session.query(Cliente).count()}"
          f" (sin repetirse entre los {total} pedidos del historico).")


def _trazar_ruta_demo(ruta):
    """Calcula la secuencia y geometria de la ruta sembrada."""
    from flask import current_app

    from app.services.ruteo import ESTRATEGIA_DISTANCIA, calcular_ruta

    origen = (current_app.config["CD_LAT"], current_app.config["CD_LNG"])
    pedidos = [p for p in ruta.pedidos if p.tiene_coordenadas]
    if not pedidos:
        return

    resultado = calcular_ruta(origen, pedidos, ESTRATEGIA_DISTANCIA)
    posiciones = {pid: i for i, pid in enumerate(resultado.orden, start=1)}

    for pedido in ruta.pedidos:
        pedido.orden_en_ruta = posiciones.get(pedido.id, pedido.orden_en_ruta)

    ruta.distancia_km = resultado.distancia_km
    ruta.duracion_min = resultado.duracion_min
    ruta.geometria = resultado.geometria
    ruta.proveedor_ruteo = resultado.proveedor
    db.session.commit()

    origen_texto = "OSRM" if resultado.proveedor == "OSRM" else "heuristica local"
    print(f"Ruta {ruta.codigo} trazada con {origen_texto}: "
          f"{ruta.distancia_km} km / {ruta.duracion_min} min")


if __name__ == "__main__":
    from run import app

    with app.app_context():
        sembrar_datos()
