"""Registro de errores del sistema (error logs).

Una fila por excepcion no controlada en una peticion. La escribe
`app.services.errores` y la consulta el administrador en Administracion >
Errores. Es un historico de solo lectura, como la auditoria.

Sin claves ajenas: el registro debe poder escribirse aunque la cuenta ya no
exista, asi que el nombre se copia. Tampoco guarda los datos del formulario
ni la consulta de la URL, que pueden traer contrasenas o tokens.
"""

from app.extensions import db
from app.tiempo import ahora


class ErrorSistema(db.Model):
    __tablename__ = "errores_sistema"

    id = db.Column(db.Integer, primary_key=True)
    registrado_en = db.Column(db.DateTime, nullable=False, default=ahora, index=True)
    metodo = db.Column(db.String(10), nullable=False)
    ruta = db.Column(db.String(500), nullable=False)
    endpoint = db.Column(db.String(80))
    usuario_id = db.Column(db.Integer, index=True)
    usuario_nombre = db.Column(db.String(120))
    ip = db.Column(db.String(45))
    # Clase de la excepcion, con su modulo si no es de Python (p. ej.
    # "sqlalchemy.exc.OperationalError").
    tipo = db.Column(db.String(200), nullable=False)
    mensaje = db.Column(db.String(1000), nullable=False)
    traza = db.Column(db.Text, nullable=False)

    def __repr__(self):
        return f"<ErrorSistema {self.id} {self.tipo}>"
