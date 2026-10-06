"""Funciones del asistente de voz del cliente destinatario.

Solo operan sobre el cliente vinculado a la cuenta de la llamada. Toda
busqueda se filtra por su `cliente_id`, asi que un pedido ajeno se responde
exactamente igual que uno inexistente: no se revela que existe.

Mismo criterio que el portal: nunca se exponen rutas, conductores, stock ni
datos de otros clientes, y la bitacora se traduce con `SEGUIMIENTO_PUBLICO`.

Fuera del alcance por voz: crear pedidos (depende de la fase del portal
pendiente). Cancelar solo desde PENDIENTE; de ASIGNADO en adelante se pide
contacto con el gestor.
"""

from flask import g

from app.controllers.asistente_api import (
    NOTA_ASISTENTE, cantidad, exigir_confirmacion, funcion_asistente, responder,
)
from app.controllers.asistente_api.comun import (
    LARGO_MAXIMO_MOTIVO, enumerar, fecha_voz, resolver, texto,
)
from app.extensions import db
from app.models import EstadoPedido, Pedido, Rol
from app.services.busqueda_voz import buscar_pedidos
from app.services.despacho import TransicionInvalida, anular_pedido
from app.services.seguimiento import SEGUIMIENTO_PUBLICO, hitos_publicos
from app.services.solicitudes import (
    SolicitudInvalida, SolicitudYaRegistrada, pendiente_de, registrar_solicitud,
)

ESPACIO = "cliente"
ROLES = (Rol.CLIENTE,)

LIMITE_LISTA = 5

NO_VINCULADO = (
    "Tu cuenta no está vinculada a un cliente activo. Comunícate con el gestor logístico."
)
NO_ENCONTRADO = "No encontré ese pedido entre los tuyos."
YA_REGISTRADA = (
    "Ya tienes una solicitud de contacto registrada; el gestor logístico se comunicará contigo."
)

PARAMETRO_PEDIDO = {
    "pedido": {"type": "string",
               "description": "Codigo del pedido o su numero del dia (\"el 3 de hoy\")."},
}


def _cliente():
    cliente = g.usuario.cliente
    if cliente is None or not cliente.activo:
        return None
    return cliente


def _publico(pedido):
    return SEGUIMIENTO_PUBLICO.get(pedido.estado, pedido.estado).lower()


def _pedido_propio(cliente):
    """Pedido del cliente por el argumento `pedido`, o (None, mensaje)."""
    buscado = texto("pedido")
    if not buscado:
        return None, "Necesito el código o el número del pedido."
    return resolver(
        buscar_pedidos(buscado, cliente_id=cliente.id),
        lambda p: f"{p.codigo} del {fecha_voz(p.fecha_despacho)}",
        "pedidos tuyos", NO_ENCONTRADO,
    )


def _con_pedido(cuerpo):
    """Resuelve cliente y pedido propio, y llama a `cuerpo(cliente, pedido)`."""
    cliente = _cliente()
    if cliente is None:
        return responder(NO_VINCULADO)
    pedido, error = _pedido_propio(cliente)
    if pedido is None:
        return responder(error)
    return cuerpo(cliente, pedido)


@funcion_asistente(
    ESPACIO, "pedidos-en-curso", roles=ROLES,
    descripcion="Pedidos del cliente que aun no se han entregado ni cancelado.",
)
def pedidos_en_curso():
    cliente = _cliente()
    if cliente is None:
        return responder(NO_VINCULADO)
    pedidos = (
        db.session.query(Pedido)
        .filter(Pedido.cliente_id == cliente.id, Pedido.estado.in_(EstadoPedido.ABIERTOS))
        .order_by(Pedido.fecha_despacho, Pedido.codigo)
        .all()
    )
    if not pedidos:
        return responder("No tienes pedidos en curso.")
    leidos = [f"{p.codigo}, {_publico(p)}, para el {fecha_voz(p.fecha_despacho)}"
              for p in pedidos[:LIMITE_LISTA]]
    sobrantes = len(pedidos) - len(leidos)
    return responder(
        f"Tienes {cantidad(len(pedidos), 'pedido')} en curso: {enumerar(leidos)}"
        + (f", y {sobrantes} más." if sobrantes else ".")
    )


@funcion_asistente(
    ESPACIO, "estado-pedido", roles=ROLES,
    descripcion="Estado de un pedido del cliente, su fecha y ventana de entrega y su historial.",
    parametros=PARAMETRO_PEDIDO, requeridos=("pedido",),
)
def estado_pedido():
    def cuerpo(cliente, pedido):
        partes = [
            f"Tu pedido {pedido.codigo}: {_publico(pedido)}. Fecha de entrega "
            f"{fecha_voz(pedido.fecha_despacho)}, ventana {pedido.ventana_texto.lower()}."
        ]
        hitos = hitos_publicos(pedido)[-3:]
        if hitos:
            partes.append(
                "Historial: "
                + "; ".join(f"{h['momento'].strftime('%d/%m %H:%M')}, {h['texto'].lower()}"
                            for h in hitos)
                + "."
            )
        return responder(" ".join(partes))
    return _con_pedido(cuerpo)


@funcion_asistente(
    ESPACIO, "ventana-entrega", roles=ROLES,
    descripcion="Fecha y ventana horaria de entrega de un pedido del cliente.",
    parametros=PARAMETRO_PEDIDO, requeridos=("pedido",),
)
def ventana_entrega():
    def cuerpo(cliente, pedido):
        return responder(
            f"Tu pedido {pedido.codigo} está programado para el "
            f"{fecha_voz(pedido.fecha_despacho)}, ventana {pedido.ventana_texto.lower()}."
        )
    return _con_pedido(cuerpo)


@funcion_asistente(
    ESPACIO, "motivo-fallo", roles=ROLES,
    descripcion="Motivo por el que no se pudo entregar un pedido del cliente.",
    parametros=PARAMETRO_PEDIDO, requeridos=("pedido",),
)
def motivo_fallo():
    def cuerpo(cliente, pedido):
        motivo = pedido.prueba_entrega.motivo_fallo if pedido.prueba_entrega else None
        if pedido.estado != EstadoPedido.FALLIDO:
            return responder(
                f"Tu pedido {pedido.codigo} no tiene una entrega fallida: está {_publico(pedido)}."
            )
        if not motivo:
            return responder(f"La entrega de tu pedido {pedido.codigo} no se logró y no hay "
                             "motivo registrado.")
        return responder(f"La entrega de tu pedido {pedido.codigo} no se logró por: {motivo}.")
    return _con_pedido(cuerpo)


@funcion_asistente(
    ESPACIO, "mis-sedes", roles=ROLES,
    descripcion="Sedes registradas del cliente con su ventana horaria habitual.",
)
def mis_sedes():
    cliente = _cliente()
    if cliente is None:
        return responder(NO_VINCULADO)
    sedes = sorted((s for s in cliente.direcciones if s.activa), key=lambda s: s.etiqueta or "")
    if not sedes:
        return responder("No tienes sedes registradas.")

    def sede_texto(s):
        ventana = (f", de {s.ventana_inicio.strftime('%H:%M')} a {s.ventana_fin.strftime('%H:%M')}"
                   if s.ventana_inicio and s.ventana_fin else "")
        return f"{s.etiqueta}, en {s.direccion}{ventana}"

    return responder(
        f"Tienes {cantidad(len(sedes), 'sede')}: " + enumerar([sede_texto(s) for s in sedes]) + "."
    )


@funcion_asistente(
    ESPACIO, "cancelar-pedido", roles=ROLES, accion=True,
    descripcion="Cancela un pedido propio que aun esta pendiente (recibido y sin programar). "
                "Si ya esta programado, hay que pedir contacto con el gestor.",
    parametros={**PARAMETRO_PEDIDO,
                "motivo": {"type": "string", "description": "Motivo de la cancelacion."}},
    requeridos=("pedido", "motivo"),
)
def cancelar_pedido():
    def cuerpo(cliente, pedido):
        if pedido.estado in (EstadoPedido.ENTREGADO, EstadoPedido.CANCELADO):
            return responder(f"Tu pedido {pedido.codigo} ya está {_publico(pedido)}; "
                             "no se puede cancelar.")
        if pedido.estado != EstadoPedido.PENDIENTE:
            return responder(
                f"Tu pedido {pedido.codigo} ya está programado para despacho. Para cancelarlo "
                "debes contactar al gestor logístico; si quieres, registro una solicitud de "
                "contacto."
            )
        motivo = texto("motivo")[:LARGO_MAXIMO_MOTIVO]
        if not motivo:
            return responder("Necesito el motivo de la cancelación.")

        pendiente = exigir_confirmacion(
            f"Voy a cancelar tu pedido {pedido.codigo} del {fecha_voz(pedido.fecha_despacho)} "
            f"por: {motivo}."
        )
        if pendiente:
            return pendiente

        try:
            anular_pedido(pedido, g.usuario.id, motivo, origen=NOTA_ASISTENTE)
        except TransicionInvalida as causa:
            db.session.rollback()
            return responder(f"No pude cancelar tu pedido {pedido.codigo}. {causa}")
        db.session.commit()
        return responder(f"Listo, tu pedido {pedido.codigo} quedó cancelado.")
    return _con_pedido(cuerpo)


@funcion_asistente(
    ESPACIO, "solicitar-contacto", roles=ROLES, accion=True,
    descripcion="Registra una solicitud para que el gestor logistico contacte al cliente.",
    parametros={"motivo": {"type": "string", "description": "Para que necesita el contacto."}},
    requeridos=("motivo",),
)
def solicitar_contacto():
    cliente = _cliente()
    if cliente is None:
        return responder(NO_VINCULADO)
    if pendiente_de(cliente) is not None:
        return responder(YA_REGISTRADA)
    motivo = texto("motivo")[:LARGO_MAXIMO_MOTIVO]
    if not motivo:
        return responder("Necesito el motivo por el que quieres que te contacten.")

    medios = [m for m in (cliente.telefono, cliente.correo) if m]
    contacto = (f" Te contactará por {enumerar(medios)}." if medios
                else " No tenemos teléfono ni correo registrados; el gestor usará los datos de tu cuenta.")
    pendiente = exigir_confirmacion(
        f"Voy a pedir que el gestor logístico te contacte por: {motivo}.{contacto}"
    )
    if pendiente:
        return pendiente

    try:
        registrar_solicitud(cliente, g.usuario.id, motivo)
    except SolicitudYaRegistrada:
        db.session.rollback()
        return responder(YA_REGISTRADA)
    except SolicitudInvalida as causa:
        db.session.rollback()
        return responder(str(causa))
    db.session.commit()
    return responder("Listo, registré tu solicitud. El gestor logístico se comunicará contigo.")
