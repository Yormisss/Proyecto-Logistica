"""Alta de ordenes de despacho (RF2).

Reglas unicas para crear un pedido, compartidas por el formulario del gestor y
el asistente de voz: ninguno de los dos valida por su cuenta. La importacion
CSV conserva su propio flujo de vista previa (`app.services.importador`).
"""

from datetime import date

from app.extensions import db
from app.models import DireccionCliente, EstadoPedido, EventoPedido, Pedido, PedidoItem, Producto
from app.services.clientes import vincular_destino
from app.services.codigos import generar_codigo_pedido
from app.services.despacho import PRIORIDADES
from app.services.validacion import DatosInvalidos, Validador
from app.tiempo import hoy

PRIORIDAD_POR_DEFECTO = 3


class PedidoInvalido(DatosInvalidos):
    """El pedido no cumple las reglas. `errores` es una lista de (campo, mensaje).

    `campo` es el nombre del campo del formulario al que pertenece el error, o
    None para los errores generales (por ejemplo, los de los productos).
    """


def validar_pedido(*, items, fecha_despacho, prioridad, ventana_inicio=None, ventana_fin=None,
                   codigo=None):
    """Valida los datos de un pedido nuevo.

    `items` es una lista de (producto_id, cantidad). Devuelve (items, errores):
    los items consolidados (un mismo producto repetido suma sus cantidades) y
    la lista de errores como (campo, mensaje); vacia si todo esta bien.
    """
    errores = []

    if fecha_despacho is None:
        errores.append(("fecha_despacho", "Indique la fecha de despacho."))

    if prioridad not in PRIORIDADES:
        errores.append(("prioridad", "La prioridad debe ser 1 (alta), 2 (media) o 3 (baja)."))

    if ventana_inicio and ventana_fin and ventana_inicio >= ventana_fin:
        errores.append(("ventana_fin", "La hora final debe ser posterior a la inicial."))

    codigo = (codigo or "").strip()
    if codigo and db.session.query(Pedido.id).filter_by(codigo=codigo).first():
        errores.append(("codigo", "Ya existe un pedido con este codigo."))

    consolidados = {}
    for producto_id, cantidad in items:
        if isinstance(cantidad, bool) or not isinstance(cantidad, int) or cantidad <= 0:
            errores.append((None, "La cantidad de cada producto debe ser un entero mayor que cero."))
            continue
        consolidados[producto_id] = consolidados.get(producto_id, 0) + cantidad

    if consolidados:
        activos = {
            producto_id
            for (producto_id,) in db.session.query(Producto.id).filter(
                Producto.id.in_(consolidados), Producto.activo.is_(True)
            )
        }
        if set(consolidados) - activos:
            errores.append((None, "Uno de los productos no existe o esta inactivo."))
    elif not errores:
        errores.append((None, "Agregue al menos un producto al pedido."))

    return list(consolidados.items()), errores


def destino_de_sede(cliente, sede):
    """Datos de entrega de un pedido para una sede ya registrada del cliente."""
    return {
        "cliente_nombre": cliente.nombre,
        "cliente_telefono": cliente.telefono,
        "direccion": sede.direccion,
        "ciudad": sede.ciudad,
        "latitud": sede.latitud,
        "longitud": sede.longitud,
        "ventana_inicio": sede.ventana_inicio,
        "ventana_fin": sede.ventana_fin,
    }


def crear_pedido(*, cliente, direccion, destino, items, fecha_despacho, prioridad, usuario_id,
                 codigo=None, observaciones=None, nota="Creado manualmente"):
    """Crea el pedido PENDIENTE con sus items y su primer evento. No hace commit.

    `cliente` y `direccion` son el Cliente y la DireccionCliente ya resueltos;
    `destino` es la copia historica de los datos de entrega (ver `Pedido`).
    Lanza PedidoInvalido si no cumple las reglas de `validar_pedido`.
    """
    codigo = (codigo or "").strip()
    items, errores = validar_pedido(
        items=items,
        fecha_despacho=fecha_despacho,
        prioridad=prioridad,
        ventana_inicio=destino.get("ventana_inicio"),
        ventana_fin=destino.get("ventana_fin"),
        codigo=codigo,
    )
    if errores:
        raise PedidoInvalido(errores)

    pedido = Pedido(
        codigo=codigo or generar_codigo_pedido(fecha_despacho),
        cliente_nombre=destino["cliente_nombre"],
        cliente_telefono=destino.get("cliente_telefono"),
        direccion=destino["direccion"],
        ciudad=destino.get("ciudad") or "Bogota",
        latitud=destino.get("latitud"),
        longitud=destino.get("longitud"),
        fecha_despacho=fecha_despacho,
        ventana_inicio=destino.get("ventana_inicio"),
        ventana_fin=destino.get("ventana_fin"),
        prioridad=prioridad,
        observaciones=observaciones or None,
        estado=EstadoPedido.PENDIENTE,
        creado_por_id=usuario_id,
    )
    vincular_destino(pedido, cliente, direccion)
    db.session.add(pedido)
    db.session.flush()

    for producto_id, cantidad in items:
        db.session.add(PedidoItem(pedido_id=pedido.id, producto_id=producto_id, cantidad=cantidad))

    db.session.add(
        EventoPedido(
            pedido_id=pedido.id,
            usuario_id=usuario_id,
            estado_nuevo=EstadoPedido.PENDIENTE,
            nota=nota,
        )
    )
    return pedido


# --------------------------------------------------------------------------
# Edicion de pedidos (pantalla y asistente de voz)
# --------------------------------------------------------------------------

# Que se puede editar en cada estado. EN_RUTA, ENTREGADO, FALLIDO y CANCELADO
# no se editan: el pedido ya salio, se cerro o se anulo.
CAMPOS_EDITABLES = {
    EstadoPedido.PENDIENTE: ("fecha_despacho", "sede_id", "ventana_inicio", "ventana_fin",
                             "prioridad", "observaciones", "items"),
    EstadoPedido.ASIGNADO: ("ventana_inicio", "ventana_fin", "prioridad", "observaciones"),
}
ETIQUETAS_CAMPO = {
    "fecha_despacho": "la fecha", "sede_id": "la sede", "ventana_inicio": "la ventana",
    "ventana_fin": "la ventana", "prioridad": "la prioridad", "observaciones": "las observaciones",
    "items": "los productos",
}
AVISO_RECALCULAR = ("El pedido ya está en una ruta: conviene recalcularla para que la "
                    "secuencia tenga en cuenta el cambio.")


def campos_editables(pedido):
    return CAMPOS_EDITABLES.get(pedido.estado, ())


def _fecha_iso(valor):
    if isinstance(valor, date):
        return valor
    try:
        return date.fromisoformat(str(valor).strip())
    except (TypeError, ValueError):
        return None


def _describir(campo, valor):
    if valor is None or valor == "":
        return "sin dato"
    if campo == "fecha_despacho":
        return valor.strftime("%d/%m/%Y")
    if campo == "prioridad":
        return PRIORIDADES.get(valor, str(valor)).lower()
    if campo in ("ventana_inicio", "ventana_fin"):
        return valor.strftime("%H:%M")
    return str(valor)


def validar_edicion(pedido, datos):
    """(cambios, nuevos_items) validados; lanza DatosInvalidos si no cumplen.

    `cambios` es {campo: (antes, despues)} solo de lo que cambia. `datos` puede
    traer fecha_despacho, sede_id, ventana_inicio, ventana_fin, prioridad,
    observaciones e items (lista de (producto_id, cantidad), que reemplaza los
    productos del pedido). Las horas de la ventana vacias quitan la ventana.
    """
    permitidos = campos_editables(pedido)
    if not permitidos:
        raise DatosInvalidos([(None, f"Un pedido {pedido.estado_etiqueta.lower()} no se puede editar.")])

    recibidos = {k: v for k, v in datos.items() if v is not None and k in ETIQUETAS_CAMPO}
    no_permitidos = sorted({ETIQUETAS_CAMPO[k] for k in recibidos if k not in permitidos})
    if no_permitidos:
        raise DatosInvalidos([(None, f"Con el pedido {pedido.estado_etiqueta.lower()} no se puede cambiar "
                                     + ", ".join(no_permitidos) + "; solo la ventana, la prioridad y "
                                     "las observaciones.")])

    v = Validador()
    cambios, items = {}, None

    if "fecha_despacho" in recibidos:
        fecha = _fecha_iso(recibidos["fecha_despacho"])
        if fecha is None:
            v.invalido("fecha_despacho", "La fecha debe tener el formato año-mes-día.")
        elif fecha < hoy():
            v.invalido("fecha_despacho", "La fecha de despacho no puede ser anterior a hoy.")
        elif fecha != pedido.fecha_despacho:
            cambios["fecha_despacho"] = (pedido.fecha_despacho, fecha)

    if "sede_id" in recibidos:
        sede = None
        try:
            sede = db.session.get(DireccionCliente, int(recibidos["sede_id"]))
        except (TypeError, ValueError):
            pass
        if pedido.cliente_id is None:
            v.invalido("sede_id", "El pedido no tiene un cliente registrado; su sede no se puede cambiar.")
        elif sede is None or sede.cliente_id != pedido.cliente_id:
            v.invalido("sede_id", "La sede debe ser una de las sedes del mismo cliente.")
        elif not sede.activa:
            v.invalido("sede_id", f"La sede {sede.etiqueta} está desactivada.")
        elif sede.id != pedido.direccion_id:
            cambios["sede_id"] = (pedido.direccion_id, sede.id)

    if "ventana_inicio" in recibidos or "ventana_fin" in recibidos:
        inicio = v.hora(recibidos, "ventana_inicio", "la hora inicial")
        fin = v.hora(recibidos, "ventana_fin", "la hora final")
        if v.ventana(inicio, fin):
            if inicio != pedido.ventana_inicio:
                cambios["ventana_inicio"] = (pedido.ventana_inicio, inicio)
            if fin != pedido.ventana_fin:
                cambios["ventana_fin"] = (pedido.ventana_fin, fin)

    if "prioridad" in recibidos:
        prioridad = v.entero(recibidos, "prioridad", "la prioridad", minimo=1, maximo=3)
        if prioridad is not None and prioridad != pedido.prioridad:
            cambios["prioridad"] = (pedido.prioridad, prioridad)

    if "observaciones" in recibidos:
        observaciones = v.texto(recibidos, "observaciones", "las observaciones", 1000)
        if (observaciones or None) != (pedido.observaciones or None):
            cambios["observaciones"] = (pedido.observaciones, observaciones)

    if "items" in recibidos:
        items, errores = validar_pedido(items=recibidos["items"], fecha_despacho=pedido.fecha_despacho,
                                        prioridad=pedido.prioridad)
        for campo, mensaje in errores:
            v.invalido(campo or "items", mensaje)
        actuales = sorted((i.producto_id, i.cantidad) for i in pedido.items)
        if not errores and sorted(items) != actuales:
            cambios["items"] = (actuales, sorted(items))
        else:
            items = None

    v.lanzar_si_hay()
    if not cambios:
        raise DatosInvalidos([(None, "No hay cambios para guardar.")])
    return cambios, items


def editar_pedido(pedido, datos, usuario_id, origen=None):
    """Aplica la edicion y la deja en la bitacora del pedido. No hace commit.

    Devuelve (cambios, advertencias). Cambiar la sede actualiza la copia
    historica de la direccion del pedido.
    """
    cambios, items = validar_edicion(pedido, datos)
    for campo, (_, nuevo) in cambios.items():
        if campo == "sede_id":
            sede = db.session.get(DireccionCliente, nuevo)
            pedido.direccion_id = sede.id
            pedido.direccion = sede.direccion
            pedido.ciudad = sede.ciudad
            pedido.latitud = sede.latitud
            pedido.longitud = sede.longitud
        elif campo == "items":
            for item in list(pedido.items):
                db.session.delete(item)
            db.session.flush()
            for producto_id, cantidad in items:
                db.session.add(PedidoItem(pedido_id=pedido.id, producto_id=producto_id, cantidad=cantidad))
        else:
            setattr(pedido, campo, nuevo)

    partes = []
    for campo, (antes, despues) in cambios.items():
        if campo == "sede_id":
            partes.append(f"sede: {pedido.direccion}")
        elif campo == "items":
            partes.append("productos actualizados")
        else:
            nombre = ETIQUETAS_CAMPO[campo].split(" ", 1)[1]
            if campo in ("ventana_inicio", "ventana_fin"):
                nombre = "inicio de la ventana" if campo == "ventana_inicio" else "fin de la ventana"
            partes.append(f"{nombre}: {_describir(campo, antes)} -> {_describir(campo, despues)}")
    nota = "Pedido editado: " + "; ".join(partes)
    if origen:
        nota = f"{nota}. {origen}"
    db.session.add(EventoPedido(pedido_id=pedido.id, usuario_id=usuario_id,
                                estado_anterior=pedido.estado, estado_nuevo=pedido.estado,
                                nota=nota[:255]))
    db.session.expire(pedido, ["items"])
    advertencias = [AVISO_RECALCULAR] if pedido.estado == EstadoPedido.ASIGNADO else []
    return cambios, advertencias
