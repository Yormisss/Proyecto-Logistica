"""Solicitudes de contacto con el gestor logistico.

Las registra el cliente desde su asistente de voz cuando necesita algo que no
puede hacer solo (por ejemplo, cancelar un pedido que ya esta asignado a una
ruta). El gestor o el admin las ven en pantalla y las marcan como atendidas.

Telefono y correo se copian del cliente al momento de la solicitud, por la
misma razon que `Pedido` copia los datos de entrega: son el contacto que se
dio entonces, aunque el cliente los cambie despues.
"""

from app.extensions import db
from app.tiempo import ahora


class SolicitudContacto(db.Model):
    __tablename__ = "solicitudes_contacto"

    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey("clientes.id"), nullable=False, index=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    motivo = db.Column(db.String(255), nullable=False)
    telefono = db.Column(db.String(30))
    correo = db.Column(db.String(120))
    creada_en = db.Column(db.DateTime, nullable=False, default=ahora, index=True)
    atendida = db.Column(db.Boolean, nullable=False, default=False, index=True)
    atendida_en = db.Column(db.DateTime)
    atendida_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"))

    cliente = db.relationship("Cliente")
    usuario = db.relationship("Usuario", foreign_keys=[usuario_id])
    atendida_por = db.relationship("Usuario", foreign_keys=[atendida_por_id])

    def __repr__(self):
        return f"<SolicitudContacto {self.id} cliente={self.cliente_id} atendida={self.atendida}>"
