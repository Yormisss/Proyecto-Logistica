"""RF1 / RNF5 - Roles con permisos configurables.

- `roles`: los cuatro del sistema (con `clave_sistema`, la de `usuarios.rol`)
  y los que crea el administrador. Cada uno con su reparto en `rol_permisos`.
- `usuario_roles`: roles adicionales de una cuenta, ademas de su rol principal.
- `usuario_permisos`: ajustes de una cuenta sobre lo que le dan sus roles:
  conceder (`concedido` = True) o denegar (False) un permiso.

El catalogo de permisos esta en el codigo (app/permisos.py) y el calculo de
los permisos de una cuenta en app/services/permisos.py.
"""

from app.extensions import db
from app.tiempo import ahora

usuario_roles = db.Table(
    "usuario_roles",
    db.Column("usuario_id", db.Integer, db.ForeignKey("usuarios.id", ondelete="CASCADE"),
              primary_key=True),
    db.Column("rol_id", db.Integer, db.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
)


class RolAcceso(db.Model):
    __tablename__ = "roles"

    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(60), nullable=False, unique=True)
    descripcion = db.Column(db.String(200))
    # ADMIN, DESPACHADOR, CONDUCTOR o CLIENTE en los roles del sistema; NULL
    # en los que crea el administrador.
    clave_sistema = db.Column(db.String(20), unique=True)
    creado_en = db.Column(db.DateTime, nullable=False, default=ahora)

    asignaciones = db.relationship("RolPermiso", cascade="all, delete-orphan",
                                   order_by="RolPermiso.permiso")
    usuarios = db.relationship("Usuario", secondary=usuario_roles, back_populates="roles_adicionales")

    @property
    def es_sistema(self):
        return self.clave_sistema is not None

    @property
    def permisos(self):
        return {a.permiso for a in self.asignaciones}

    def __repr__(self):
        return f"<RolAcceso {self.nombre}>"


class RolPermiso(db.Model):
    __tablename__ = "rol_permisos"

    rol_id = db.Column(db.Integer, db.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    permiso = db.Column(db.String(40), primary_key=True)


class PermisoUsuario(db.Model):
    __tablename__ = "usuario_permisos"

    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id", ondelete="CASCADE"),
                           primary_key=True)
    permiso = db.Column(db.String(40), primary_key=True)
    # True: se le concede aunque sus roles no lo den. False: se le niega
    # aunque sus roles lo den.
    concedido = db.Column(db.Boolean, nullable=False)
