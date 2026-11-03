"""RNF5 - Contador de intentos fallidos de inicio de sesion.

Una fila por correo con fallos recientes. La clave es el correo tal como se
escribio, no una clave ajena a `usuarios`: asi tambien cuentan los correos que
no existen. Si solo se contaran las cuentas reales, el bloqueo revelaria
cuales existen, que es justo lo que el mensaje generico del login evita. Por
la misma razon la tabla aparece suelta en el diagrama EER.

La logica del bloqueo vive en `app/services/acceso.py`.
"""

from app.extensions import db


class IntentoAcceso(db.Model):
    __tablename__ = "intentos_acceso"

    correo = db.Column(db.String(120), primary_key=True)
    fallidos = db.Column(db.Integer, nullable=False, default=0)
    ultimo_fallo_en = db.Column(db.DateTime, nullable=False)
    # Hasta cuando se rechaza el login de este correo, aun con la contrasena
    # correcta. NULL mientras no se alcance el maximo de intentos.
    bloqueado_hasta = db.Column(db.DateTime)

    def __repr__(self):
        return f"<IntentoAcceso {self.correo} ({self.fallidos})>"
