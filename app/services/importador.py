"""RF2 - Importacion masiva de ordenes de despacho desde archivo CSV.

Responde a la limitacion de "Infraestructura tecnologica heredada" (numeral 1.4):
el ERP legado no expone APIs modernas, por lo que la integracion se realiza
mediante exportacion de archivos planos.

Formato esperado (una fila por producto; las filas que comparten `codigo` se
agrupan en un mismo pedido):

    codigo,cliente_nombre,cliente_documento,cliente_telefono,direccion,ciudad,
    latitud,longitud,fecha_despacho,ventana_inicio,ventana_fin,prioridad,sku,
    cantidad,observaciones

Los clientes y sus sedes no se duplican: cada fila se resuelve contra las tablas
`clientes` y `direcciones_cliente` usando el NIT cuando viene y el nombre
normalizado cuando no (ver `app.services.clientes`).
"""

import csv
import io
from datetime import datetime

from app.extensions import db
from app.models import EstadoPedido, Pedido, PedidoItem, Producto
from app.services.clientes import Resolutor, existe_cliente, vincular_destino
from app.services.codigos import generar_codigo_pedido
from app.tiempo import hoy

COLUMNAS_REQUERIDAS = {"cliente_nombre", "direccion", "sku", "cantidad"}

COLUMNAS_RECONOCIDAS = [
    "codigo", "cliente_nombre", "cliente_documento", "cliente_telefono",
    "direccion", "ciudad", "latitud", "longitud", "fecha_despacho",
    "ventana_inicio", "ventana_fin", "prioridad", "sku", "cantidad",
    "observaciones",
]

FORMATOS_FECHA = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y")
FORMATOS_HORA = ("%H:%M", "%H:%M:%S", "%H.%M")


class ErrorImportacion(Exception):
    """Error que impide procesar el archivo completo."""


def _normalizar_encabezados(campos):
    return [(c or "").strip().lower().replace(" ", "_").lstrip("﻿") for c in campos]


def _leer_fecha(valor, linea):
    if not valor:
        return hoy()
    for formato in FORMATOS_FECHA:
        try:
            return datetime.strptime(valor, formato).date()
        except ValueError:
            continue
    raise ValueError(f"fecha_despacho '{valor}' no tiene un formato valido (AAAA-MM-DD)")


def _leer_hora(valor, campo):
    if not valor:
        return None
    for formato in FORMATOS_HORA:
        try:
            return datetime.strptime(valor, formato).time()
        except ValueError:
            continue
    raise ValueError(f"{campo} '{valor}' no tiene un formato valido (HH:MM)")


def _leer_decimal(valor, campo):
    if valor in (None, ""):
        return None
    try:
        return float(str(valor).replace(",", "."))
    except ValueError:
        raise ValueError(f"{campo} '{valor}' no es un numero valido")


def _leer_entero(valor, campo, por_defecto=None):
    if valor in (None, ""):
        if por_defecto is None:
            raise ValueError(f"{campo} es obligatorio")
        return por_defecto
    try:
        return int(float(str(valor).replace(",", ".")))
    except ValueError:
        raise ValueError(f"{campo} '{valor}' no es un numero entero valido")


def analizar_csv(contenido_binario):
    """Valida el archivo y devuelve (pedidos_validos, errores).

    No escribe en la base de datos: permite mostrar una vista previa al
    despachador antes de confirmar la carga.
    """
    try:
        texto = contenido_binario.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            texto = contenido_binario.decode("latin-1")
        except UnicodeDecodeError:
            raise ErrorImportacion("No se pudo leer el archivo. Guardelo con codificacion UTF-8.")

    if not texto.strip():
        raise ErrorImportacion("El archivo esta vacio.")

    # Detecta el separador (coma o punto y coma, comun en Excel en espanol).
    try:
        dialecto = csv.Sniffer().sniff(texto[:2048], delimiters=",;\t")
    except csv.Error:
        dialecto = csv.excel

    lector = csv.reader(io.StringIO(texto), dialecto)
    filas = list(lector)
    if len(filas) < 2:
        raise ErrorImportacion("El archivo no contiene filas de datos ademas del encabezado.")

    encabezados = _normalizar_encabezados(filas[0])
    faltantes = COLUMNAS_REQUERIDAS - set(encabezados)
    if faltantes:
        raise ErrorImportacion(
            "Faltan columnas obligatorias en el encabezado: " + ", ".join(sorted(faltantes))
        )

    # Catalogo de productos indexado por SKU (una sola consulta).
    productos = {
        p.sku.upper(): p
        for p in db.session.query(Producto).filter(Producto.activo.is_(True)).all()
    }
    codigos_existentes = {c for (c,) in db.session.query(Pedido.codigo).all()}

    agrupados = {}   # clave -> dict del pedido
    errores = []
    orden_llegada = 0

    for numero_linea, fila in enumerate(filas[1:], start=2):
        if not any((celda or "").strip() for celda in fila):
            continue  # Fila en blanco

        datos = dict(zip(encabezados, [(c or "").strip() for c in fila]))

        try:
            cliente = datos.get("cliente_nombre", "")
            direccion = datos.get("direccion", "")
            sku = datos.get("sku", "").upper()

            if not cliente:
                raise ValueError("cliente_nombre es obligatorio")
            if not direccion:
                raise ValueError("direccion es obligatoria")
            if not sku:
                raise ValueError("sku es obligatorio")
            if sku not in productos:
                raise ValueError(f"el SKU '{sku}' no existe en el inventario")

            cantidad = _leer_entero(datos.get("cantidad"), "cantidad")
            if cantidad <= 0:
                raise ValueError("cantidad debe ser mayor que cero")

            fecha_despacho = _leer_fecha(datos.get("fecha_despacho"), numero_linea)
            ventana_inicio = _leer_hora(datos.get("ventana_inicio"), "ventana_inicio")
            ventana_fin = _leer_hora(datos.get("ventana_fin"), "ventana_fin")
            if ventana_inicio and ventana_fin and ventana_inicio >= ventana_fin:
                raise ValueError("ventana_inicio debe ser anterior a ventana_fin")

            prioridad = _leer_entero(datos.get("prioridad"), "prioridad", por_defecto=3)
            if prioridad not in (1, 2, 3):
                raise ValueError("prioridad debe ser 1 (alta), 2 (media) o 3 (baja)")

            latitud = _leer_decimal(datos.get("latitud"), "latitud")
            longitud = _leer_decimal(datos.get("longitud"), "longitud")
            if latitud is not None and not (-90 <= latitud <= 90):
                raise ValueError("latitud fuera de rango (-90 a 90)")
            if longitud is not None and not (-180 <= longitud <= 180):
                raise ValueError("longitud fuera de rango (-180 a 180)")

            codigo = datos.get("codigo", "").strip()
            if codigo and codigo in codigos_existentes:
                raise ValueError(f"el codigo '{codigo}' ya existe en el sistema")

            # Las filas sin codigo se tratan como pedidos independientes.
            clave = codigo or f"__linea_{numero_linea}"

            if clave not in agrupados:
                orden_llegada += 1
                documento = datos.get("cliente_documento") or None
                agrupados[clave] = {
                    "orden": orden_llegada,
                    "codigo": codigo,
                    "cliente_nombre": cliente,
                    "cliente_documento": documento,
                    # La vista previa avisa al despachador cuando la carga va a
                    # crear un cliente nuevo, para que detecte un nombre mal
                    # escrito antes de confirmar.
                    "cliente_existente": existe_cliente(cliente, documento),
                    "cliente_telefono": datos.get("cliente_telefono") or None,
                    "direccion": direccion,
                    "ciudad": datos.get("ciudad") or "Bogota",
                    "latitud": latitud,
                    "longitud": longitud,
                    "fecha_despacho": fecha_despacho,
                    "ventana_inicio": ventana_inicio,
                    "ventana_fin": ventana_fin,
                    "prioridad": prioridad,
                    "observaciones": datos.get("observaciones") or None,
                    "items": [],
                    "lineas": [],
                }

            pedido = agrupados[clave]
            pedido["lineas"].append(numero_linea)

            # Si el mismo SKU se repite dentro del pedido, se suman las cantidades.
            for item in pedido["items"]:
                if item["sku"] == sku:
                    item["cantidad"] += cantidad
                    break
            else:
                pedido["items"].append(
                    {
                        "sku": sku,
                        "producto_id": productos[sku].id,
                        "nombre": productos[sku].nombre,
                        "cantidad": cantidad,
                    }
                )

        except ValueError as error:
            errores.append({"linea": numero_linea, "mensaje": str(error)})

    pedidos = sorted(agrupados.values(), key=lambda p: p["orden"])
    return pedidos, errores


def guardar_pedidos(pedidos, usuario_id):
    """Persiste los pedidos ya validados. Devuelve la cantidad creada."""
    from app.models import EventoPedido

    # Un solo resolutor para todo el lote: las filas repetidas del mismo cliente
    # se resuelven en memoria en vez de consultar la base una vez por fila.
    resolutor = Resolutor()
    creados = 0
    for datos in pedidos:
        codigo = datos["codigo"] or generar_codigo_pedido(datos["fecha_despacho"])

        cliente = resolutor.cliente(
            datos["cliente_nombre"],
            documento=datos.get("cliente_documento"),
            telefono=datos["cliente_telefono"],
        )
        direccion = resolutor.direccion(
            cliente,
            datos["direccion"],
            ciudad=datos["ciudad"],
            latitud=datos["latitud"],
            longitud=datos["longitud"],
            ventana_inicio=datos["ventana_inicio"],
            ventana_fin=datos["ventana_fin"],
        )

        pedido = Pedido(
            codigo=codigo,
            cliente_nombre=datos["cliente_nombre"],
            cliente_telefono=datos["cliente_telefono"],
            direccion=datos["direccion"],
            ciudad=datos["ciudad"],
            latitud=datos["latitud"],
            longitud=datos["longitud"],
            fecha_despacho=datos["fecha_despacho"],
            ventana_inicio=datos["ventana_inicio"],
            ventana_fin=datos["ventana_fin"],
            prioridad=datos["prioridad"],
            observaciones=datos["observaciones"],
            estado=EstadoPedido.PENDIENTE,
            creado_por_id=usuario_id,
        )
        vincular_destino(pedido, cliente, direccion)
        db.session.add(pedido)
        db.session.flush()

        for item in datos["items"]:
            db.session.add(
                PedidoItem(
                    pedido_id=pedido.id,
                    producto_id=item["producto_id"],
                    cantidad=item["cantidad"],
                )
            )

        db.session.add(
            EventoPedido(
                pedido_id=pedido.id,
                usuario_id=usuario_id,
                estado_nuevo=EstadoPedido.PENDIENTE,
                nota="Creado por importacion CSV",
            )
        )
        creados += 1

    db.session.commit()
    return creados


def generar_plantilla_csv():
    """Plantilla descargable con el formato esperado y filas de ejemplo."""
    salida = io.StringIO()
    escritor = csv.writer(salida)
    escritor.writerow(COLUMNAS_RECONOCIDAS)
    escritor.writerow([
        "", "Supermercado La 80", "900123456-1", "3115550101", "Cra 80 #45-12",
        "Bogota", "4.6712", "-74.0912", hoy().strftime("%Y-%m-%d"),
        "08:00", "12:00", "2", "SKU-1001", "10", "Entregar en muelle de carga",
    ])
    escritor.writerow([
        "", "Supermercado La 80", "900123456-1", "3115550101", "Cra 80 #45-12",
        "Bogota", "4.6712", "-74.0912", hoy().strftime("%Y-%m-%d"),
        "08:00", "12:00", "2", "SKU-1002", "5", "",
    ])
    return salida.getvalue()
