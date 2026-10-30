"""RF1 - Gestion de Usuarios y Roles.

Modela los actores identificados en el numeral 2.2 del documento que interactuan
directamente con la plataforma: administradores del centro de distribucion,
gestores logisticos, conductores de la flota y los clientes destinatarios que
consultan el estado de sus propias ordenes.
"""

from flask_login import UserMixin
from sqlalchemy.orm import validates
from werkzeug.security import check_password_hash, generate_password_hash

from app.cifrado import TextoCifrado, indice_ciego
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

    # Roles cuya contrasena administra el administrador. Fuera quedan el
    # cliente, que solo cambia la suya, y los demas administradores; la propia
    # cada administrador la cambia con la actual.
    CONTRASENA_ADMINISTRADA = (DESPACHADOR, CONDUCTOR)

    @classmethod
    def opciones(cls):
        return list(cls.ETIQUETAS.items())


class Usuario(UserMixin, db.Model):
    __tablename__ = "usuarios"

    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(120), nullable=False)
    correo = db.Column(db.String(120), unique=True, nullable=False, index=True)
    # RNF5 - Datos personales cifrados (ver app/cifrado.py). Hasta 30 caracteres
    # en claro; el texto cifrado ocupa hasta 250. La unicidad del documento la
    # garantiza su indice ciego, que `_indexar_documento` mantiene al dia.
    documento = db.Column(TextoCifrado(300))
    documento_indice = db.Column(db.String(64), unique=True, index=True)
    telefono = db.Column(TextoCifrado(300))
    # RNF5 - Seguridad de Acceso: nunca se almacena la contrasena en texto plano.
    contrasena_hash = db.Column(db.String(255), nullable=False)
    rol = db.Column(db.String(20), nullable=False, default=Rol.CONDUCTOR, index=True)
    activo = db.Column(db.Boolean, nullable=False, default=True)
    creado_en = db.Column(db.DateTime, default=ahora)
    # Gestion de contrasenas del administrador. Las cuentas anteriores a la
    # migracion m008 quedan con la fecha en NULL: no se sabe cuando cambio.
    contrasena_actualizada_en = db.Column(db.DateTime, default=ahora)
    # True cuando la contrasena la asigno el administrador: solo sirve para
    # entrar y reemplazarla (ver `auth.exigir_cambio_contrasena`).
    debe_cambiar_contrasena = db.Column(db.Boolean, nullable=False, default=False)
    # Forma parte del identificador de sesion (`get_id`): cada cambio de
    # contrasena la incrementa y las sesiones abiertas con la anterior caducan.
    version_credencial = db.Column(db.Integer, nullable=False, default=0)
    # Sesion unica e inactividad (ver app/services/sesiones.py). El token
    # tambien va en `get_id`: cada inicio de sesion lo renueva y la sesion de
    # otro navegador caduca. NULL = sin sesion abierta.
    token_sesion = db.Column(db.String(64))
    sesion_actividad_en = db.Column(db.DateTime)

    rutas = db.relationship("Ruta", back_populates="conductor", lazy="dynamic")
    vehiculo = db.relationship("Vehiculo", back_populates="conductor", uselist=False)
    # Relacion 1 a 0..1 con la entidad comercial. El dueno de la clave ajena es
    # `Cliente`, para que un cliente pueda existir sin cuenta de acceso.
    cliente = db.relationship("Cliente", back_populates="usuario", uselist=False)
    # Permisos configurables (ver app/models/permiso.py): roles ademas del
    # principal (`rol`) y ajustes propios, permiso por permiso.
    roles_adicionales = db.relationship("RolAcceso", secondary="usuario_roles",
                                        back_populates="usuarios", order_by="RolAcceso.nombre")
    ajustes_permisos = db.relationship("PermisoUsuario", cascade="all, delete-orphan",
                                       order_by="PermisoUsuario.permiso")

    # --- Seguridad (RNF5) -------------------------------------------------
    @validates("documento")
    def _indexar_documento(self, _campo, valor):
        self.documento_indice = indice_ciego(valor)
        return valor

    def establecer_contrasena(self, contrasena, temporal=False):
        """Guarda el hash de `contrasena` y cierra las sesiones abiertas.

        `temporal=True` marca una contrasena asignada por el administrador: el
        usuario debera reemplazarla antes de operar. Quien la cambia desde su
        perfil vuelve a iniciar sesion en esa misma peticion (`auth.perfil`).
        """
        self.contrasena_hash = generate_password_hash(contrasena, method=METODO_HASH)
        self.contrasena_actualizada_en = ahora()
        self.debe_cambiar_contrasena = temporal
        self.version_credencial = (self.version_credencial or 0) + 1

    def verificar_contrasena(self, contrasena):
        return check_password_hash(self.contrasena_hash, contrasena)

    @property
    def hash_vigente(self):
        """True si el hash usa la politica actual (`METODO_HASH`)."""
        return self.contrasena_hash.startswith(METODO_HASH + "$")

    @property
    def algoritmo_hash(self):
        return self.contrasena_hash.split("$", 1)[0]

    def get_id(self):
        # Flask-Login guarda este valor en la cookie de sesion y en la de
        # "mantener sesion iniciada". Con la version de la credencial, una
        # sesion abierta antes de restablecer la contrasena deja de valer; con
        # el token, la de otro navegador tras un nuevo inicio de sesion.
        return f"{self.id}:{self.version_credencial or 0}:{self.token_sesion or ''}"

    # --- Autorizacion -----------------------------------------------------
    def puede(self, permiso):
        """True si la cuenta tiene `permiso` (ver app/services/permisos.py)."""
        from app.services.permisos import permisos_efectivos

        return permiso in permisos_efectivos(self)

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
    def contrasena_administrable(self):
        """True si el administrador puede asignar o restablecer esta contrasena."""
        return self.rol in Rol.CONTRASENA_ADMINISTRADA

    @property
    def rol_etiqueta(self):
        return Rol.ETIQUETAS.get(self.rol, self.rol)

    def __repr__(self):
        return f"<Usuario {self.correo} ({self.rol})>"


@login_manager.user_loader
def cargar_usuario(identificador):
    """Recarga el usuario de la sesion en cada peticion (RNF5).

    Devolver None cuando la cuenta esta inactiva hace que Flask-Login trate la
    sesion como anonima: sin esto, una cuenta desactivada mientras el usuario
    la tiene abierta en el navegador conservaba el acceso hasta que volviera a
    iniciar sesion, porque el login ya la rechaza pero nada revisaba una
    sesion ya autenticada. Lo mismo con una sesion abierta antes de que se
    cambiara la contrasena (su version de credencial ya no coincide) o antes
    de que la cuenta entrara en otro navegador (su token ya no coincide).
    """
    usuario_id, version, token = partes_identificador(identificador)
    if usuario_id is None:
        return None
    usuario = db.session.get(Usuario, usuario_id)
    if usuario is None or not usuario.activo:
        return None
    if version != str(usuario.version_credencial or 0):
        return None
    if token != (usuario.token_sesion or ""):
        return None
    return usuario


def partes_identificador(identificador):
    """(id, version, token) del identificador de `get_id`; id None si no sirve.

    Una sesion abierta antes de la sesion unica trae solo "id:version": su
    token vacio coincide con el de una cuenta que aun no ha vuelto a entrar.
    """
    id_texto, _, resto = (identificador or "").partition(":")
    version, _, token = resto.partition(":")
    try:
        return int(id_texto), version, token
    except ValueError:
        return None, version, token
