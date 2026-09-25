"""RF1 - Gestion de Usuarios y Roles.

Modela los actores identificados en el numeral 2.2 del documento que interactuan
directamente con la plataforma: administradores del centro de distribucion,
gestores logisticos, conductores de la flota y los clientes destinatarios que
consultan el estado de sus propias ordenes.
"""

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db, login_manager
from app.tiempo import ahora


# RNF5 - Seguridad de Acceso.
# Se usa PBKDF2-SHA256 con 600.000 iteraciones (recomendacion OWASP) en lugar del
# scrypt por defecto de Werkzeug, porque el Python del sistema en macOS se compila
# contra LibreSSL y no expone hashlib.scrypt.
METODO_HASH = "pbkdf2:sha256:600000"


class Rol:
    """Roles operativos diferenciados por el sistema (RF1)."""

    ADMIN = "ADMIN"          # 2.2.1 Administradores del Centro de Distribucion
    DESPACHADOR = "DESPACHADOR"  # 2.2.3 Gestores Logisticos / Despachadores
    CONDUCTOR = "CONDUCTOR"  # 2.2.2 Conductores y Flota de Transporte
    CLIENTE = "CLIENTE"      # 2.2.4 Clientes destinatarios (portal de seguimiento)

    ETIQUETAS = {
        ADMIN: "Administrador",
        DESPACHADOR: "Gestor logistico",
        CONDUCTOR: "Conductor",
        CLIENTE: "Cliente",
    }

    # Roles que operan el centro de distribucion. El cliente queda fuera a
    # proposito: solo consulta sus propias ordenes.
    INTERNOS = (ADMIN, DESPACHADOR, CONDUCTOR)

    @classmethod
    def opciones(cls):
        return list(cls.ETIQUETAS.items())


class Usuario(UserMixin, db.Model):
    __tablename__ = "usuarios"

    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(120), nullable=False)
    correo = db.Column(db.String(120), unique=True, nullable=False, index=True)
    documento = db.Column(db.String(30), unique=True)
    telefono = db.Column(db.String(30))
    # RNF5 - Seguridad de Acceso: nunca se almacena la contrasena en texto plano.
    contrasena_hash = db.Column(db.String(255), nullable=False)
    rol = db.Column(db.String(20), nullable=False, default=Rol.CONDUCTOR, index=True)
    activo = db.Column(db.Boolean, nullable=False, default=True)
    creado_en = db.Column(db.DateTime, default=ahora)

    rutas = db.relationship("Ruta", back_populates="conductor", lazy="dynamic")
    vehiculo = db.relationship("Vehiculo", back_populates="conductor", uselist=False)
    # Relacion 1 a 0..1 con la entidad comercial. El dueno de la clave ajena es
    # `Cliente`, para que un cliente pueda existir sin cuenta de acceso.
    cliente = db.relationship("Cliente", back_populates="usuario", uselist=False)

    # --- Seguridad (RNF5) -------------------------------------------------
    def establecer_contrasena(self, contrasena):
        self.contrasena_hash = generate_password_hash(contrasena, method=METODO_HASH)

    def verificar_contrasena(self, contrasena):
        return check_password_hash(self.contrasena_hash, contrasena)

    # --- Autorizacion -----------------------------------------------------
    @property
    def es_admin(self):
        return self.rol == Rol.ADMIN

    @property
    def es_despachador(self):
        return self.rol in (Rol.ADMIN, Rol.DESPACHADOR)

    @property
    def es_conductor(self):
        return self.rol == Rol.CONDUCTOR

    @property
    def es_cliente(self):
        return self.rol == Rol.CLIENTE

    @property
    def es_interno(self):
        """Personal del centro de distribucion, en oposicion al cliente externo."""
        return self.rol in Rol.INTERNOS

    @property
    def rol_etiqueta(self):
        return Rol.ETIQUETAS.get(self.rol, self.rol)

    def __repr__(self):
        return f"<Usuario {self.correo} ({self.rol})>"


@login_manager.user_loader
def cargar_usuario(usuario_id):
    return db.session.get(Usuario, int(usuario_id))
