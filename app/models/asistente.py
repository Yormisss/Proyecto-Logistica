"""Sesiones del asistente de voz (Retell AI).

Retell invoca las custom functions desde sus servidores, sin la cookie de sesion
del navegador: lo unico que identifica al usuario es el `call_id` de la llamada
web. Esta tabla vincula ese `call_id` con el usuario que la inicio desde una
sesion autenticada, para que cada funcion opere solo sobre los datos de ese
usuario y solo mientras la sesion este vigente.
"""

from datetime import timedelta

from app.extensions import db
from app.tiempo import ahora

# Vigencia del vinculo call_id -> usuario. Pasado este plazo las custom
# functions de la llamada responden 403 aunque la llamada siga abierta.
VIGENCIA_SESION = timedelta(minutes=10)

# Plazo para confirmar una accion despues de oir su resumen.
VIGENCIA_CONFIRMACION = timedelta(minutes=3)


class SesionAsistente(db.Model):
    __tablename__ = "sesiones_asistente"

    id = db.Column(db.Integer, primary_key=True)
    call_id = db.Column(db.String(64), unique=True, nullable=False, index=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    # Rol con el que se abrio la llamada. Se guarda aparte del usuario para que
    # un cambio de rol posterior no habilite funciones de otro agente.
    rol = db.Column(db.String(20), nullable=False)
    creada_en = db.Column(db.DateTime, nullable=False, default=ahora)
    vence_en = db.Column(db.DateTime, nullable=False, index=True)
    # Accion resumida y pendiente de confirmar en esta llamada: huella de la
    # funcion y sus argumentos. Solo hay una a la vez; se consume al ejecutarse.
    confirmacion_firma = db.Column(db.String(64))
    confirmacion_vence_en = db.Column(db.DateTime)

    usuario = db.relationship("Usuario")

    @property
    def vigente(self):
        return ahora() < self.vence_en

    def __repr__(self):
        return f"<SesionAsistente {self.call_id} usuario={self.usuario_id}>"
