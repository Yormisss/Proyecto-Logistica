"""Solicitudes de contacto de los clientes con el gestor logistico.

Las registra el asistente de voz del cliente; el gestor y el admin las
consultan en pantalla (o por voz) y las marcan como atendidas.
"""

from app.extensions import db
from app.models import SolicitudContacto
from app.services.notificaciones import aviso_solicitud_contacto
from app.tiempo import ahora


class SolicitudInvalida(Exception):
    """La solicitud no se puede registrar o atender."""


def registrar_solicitud(cliente, usuario_id, motivo):
    """Registra la solicitud y encola el aviso a operaciones. No hace commit."""
    motivo = (motivo or "").strip()
    if not motivo:
        raise SolicitudInvalida("Indique el motivo de la solicitud.")
    solicitud = SolicitudContacto(
        cliente=cliente,
        usuario_id=usuario_id,
        motivo=motivo[:255],
        telefono=cliente.telefono,
        correo=cliente.correo,
        creada_en=ahora(),
    )
    db.session.add(solicitud)
    db.session.flush()
    aviso_solicitud_contacto(solicitud)
    return solicitud


def pendientes():
    """Solicitudes sin atender, la mas antigua primero."""
    return (
        db.session.query(SolicitudContacto)
        .filter(SolicitudContacto.atendida.is_(False))
        .order_by(SolicitudContacto.creada_en, SolicitudContacto.id)
        .all()
    )


def marcar_atendida(solicitud, usuario_id):
    """Marca la solicitud como atendida. No hace commit."""
    if solicitud.atendida:
        raise SolicitudInvalida("La solicitud ya estaba atendida.")
    solicitud.atendida = True
    solicitud.atendida_en = ahora()
    solicitud.atendida_por_id = usuario_id
