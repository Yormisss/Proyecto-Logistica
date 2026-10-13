"""Configuracion del negocio, editable por el administrador.

A diferencia de la configuracion del sistema (claves, tokens, base de datos y
URLs, que viven solo en el .env), estos datos los ajusta la operacion: el
catalogo de motivos de entrega fallida y el centro de distribucion desde el
que salen las rutas.
"""

from app.extensions import db
from app.tiempo import ahora

# El motivo comodin: siempre existe, no se renombra ni se desactiva, y al
# elegirlo hay que escribir el motivo real.
NOMBRE_OTRO = "Otro"


class MotivoFallo(db.Model):
    """Motivo de entrega fallida del catalogo.

    No se borran: un motivo que ya se uso es parte del historico de las pruebas
    de entrega. Se desactivan, y dejan de ofrecerse al conductor.
    """

    __tablename__ = "motivos_fallo"

    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(160), nullable=False)
    nombre_normalizado = db.Column(db.String(160), nullable=False, unique=True, index=True)
    # Nombre normalizado con el que se creo. Permite reconocer las pruebas de
    # entrega anteriores al catalogo (que solo guardan el texto) aunque el
    # motivo se haya renombrado despues.
    clave_original = db.Column(db.String(160), nullable=False, index=True)
    activo = db.Column(db.Boolean, nullable=False, default=True)
    es_otro = db.Column(db.Boolean, nullable=False, default=False)
    orden = db.Column(db.Integer, nullable=False, default=0)
    creado_en = db.Column(db.DateTime, nullable=False, default=ahora)

    def __repr__(self):
        return f"<MotivoFallo {self.nombre!r} activo={self.activo}>"


class ConfiguracionNegocio(db.Model):
    """Fila unica con el centro de distribucion.

    La primera vez se toma de CD_NOMBRE, CD_LAT y CD_LNG del .env; desde que
    existe la fila, manda la tabla.
    """

    __tablename__ = "configuracion_negocio"

    id = db.Column(db.Integer, primary_key=True)
    cd_nombre = db.Column(db.String(120), nullable=False)
    # Double: en MySQL, Float es FLOAT de precision simple y redondea las
    # coordenadas a unos 7 digitos (4.6482837 se guardaria como 4.64828).
    cd_latitud = db.Column(db.Double, nullable=False)
    cd_longitud = db.Column(db.Double, nullable=False)
    actualizado_en = db.Column(db.DateTime, nullable=False, default=ahora)
    actualizado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"))

    actualizado_por = db.relationship("Usuario")
