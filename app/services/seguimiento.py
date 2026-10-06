"""Lo que el cliente destinatario ve del avance de su pedido.

Lo comparten el portal del cliente y su asistente de voz: los dos traducen la
bitacora con los mismos textos y omiten lo mismo (usuario que registro el
evento, notas internas y eventos que no cambian el estado, como un cambio de
prioridad).
"""

from app.models import EstadoPedido

# Texto que ve el cliente para cada estado interno. La bitacora se traduce en
# vez de mostrarse cruda: notas como "Creado por importacion CSV" son de uso
# interno y no aportan nada al destinatario.
SEGUIMIENTO_PUBLICO = {
    EstadoPedido.PENDIENTE: "Pedido recibido",
    EstadoPedido.ASIGNADO: "Programado para despacho",
    EstadoPedido.EN_RUTA: "En camino a su direccion",
    EstadoPedido.ENTREGADO: "Entregado",
    EstadoPedido.FALLIDO: "Entrega no lograda",
    EstadoPedido.CANCELADO: "Pedido anulado",
}


def hitos_publicos(pedido):
    """Bitacora depurada: solo los cambios de estado, con su texto y momento."""
    return [
        {
            "texto": SEGUIMIENTO_PUBLICO.get(evento.estado_nuevo, evento.estado_nuevo),
            "estado": evento.estado_nuevo,
            "momento": evento.registrado_en,
        }
        for evento in pedido.eventos
        if evento.estado_anterior != evento.estado_nuevo
    ]
