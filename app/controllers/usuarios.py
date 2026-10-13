"""RF1 - Administracion de cuentas de acceso.

Cierra el circuito del RF1: hasta ahora los usuarios solo podian nacer en
`seed.py`, de modo que dar de alta a un conductor o habilitar el portal de un
cliente exigia entrar a la base a mano.

Las cuentas **no se eliminan**, se desactivan. `Usuario` es el origen de las
claves ajenas de `rutas`, `eventos_pedido` y `movimientos_inventario`: borrar una
cuenta romperia la trazabilidad que sostiene el RF6. El login ya rechaza a los
usuarios inactivos, asi que desactivar es el equivalente funcional de eliminar
sin perder el historico.

Las reglas viven en `app.services.usuarios`, que tambien usa el asistente de voz.
Toda accion sobre una cuenta queda en la auditoria (sin la contrasena).
"""

from flask import (
    Blueprint, abort, flash, redirect, render_template, request, url_for,
)
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from sqlalchemy import func, or_
from wtforms import BooleanField, PasswordField, SelectField, StringField
from wtforms.validators import Optional

from app.controllers.seguridad import requiere_rol
from app.extensions import db
from app.models import Cliente, Rol, Usuario
from app.services.auditoria import cambios_legibles, registrar_auditoria
from app.services.usuarios import (
    LONGITUD_MINIMA, cambiar_estado_usuario, clientes_disponibles, crear_usuario,
    editar_usuario, restablecer_contrasena, rutas_abiertas,
)
from app.services.validacion import DatosInvalidos

usuarios_bp = Blueprint("usuarios", __name__)

SIN_CLIENTE = 0


def _a_entero(valor):
    """Coercion tolerante del select de clientes: un valor raro es "sin cliente"."""
    try:
        return int(valor)
    except (TypeError, ValueError):
        return SIN_CLIENTE


class FormularioUsuario(FlaskForm):
    # Sin validadores propios: las reglas (obligatorios, largos, correo,
    # unicidad, rol, cliente, contrasena) las aplica el servicio, igual que por voz.
    nombre = StringField("Nombre completo", validators=[Optional()])
    correo = StringField("Correo electronico", validators=[Optional()])
    documento = StringField("Documento", validators=[Optional()])
    telefono = StringField("Telefono", validators=[Optional()])
    # `validate_choice=False`: la comprobacion de WTForms responde en ingles.
    rol = SelectField("Rol", validate_choice=False, validators=[Optional()])
    # Solo aplica al rol CLIENTE: es la entidad comercial cuyos pedidos vera.
    cliente_id = SelectField(
        "Cliente asociado", coerce=_a_entero, validate_choice=False, validators=[Optional()],
    )
    contrasena = PasswordField("Contrasena", validators=[Optional()])
    activo = BooleanField("Cuenta activa", default=True)

    def __init__(self, *args, usuario=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.rol.choices = Rol.opciones()
        self.cliente_id.choices = [(SIN_CLIENTE, "— Seleccione el cliente —")] + [
            (c.id, f"{c.nombre}" + (f" · NIT {c.documento}" if c.documento else ""))
            for c in clientes_disponibles(usuario)
        ]

    def datos(self):
        return {
            "nombre": self.nombre.data, "correo": self.correo.data, "documento": self.documento.data,
            "telefono": self.telefono.data, "rol": self.rol.data, "cliente_id": self.cliente_id.data,
            "contrasena": self.contrasena.data or None, "activo": self.activo.data,
        }


class FormularioAccion(FlaskForm):
    """Acciones POST sin datos propios; solo aporta el token CSRF."""


def _mostrar_errores(formulario, error):
    """Ubica cada error del servicio junto a su campo, o como aviso general."""
    for campo, mensaje in error.errores:
        destino = getattr(formulario, campo, None) if campo else None
        if destino is not None:
            destino.errors = list(destino.errors) + [mensaje]
        else:
            flash(mensaje, "error")


def _datos_cuenta(usuario, cliente=None):
    datos = {"nombre": usuario.nombre, "correo": usuario.correo, "rol": usuario.rol_etiqueta,
             "activo": usuario.activo}
    if cliente is not None:
        datos["cliente"] = cliente.nombre
    return datos


def _usuario(usuario_id):
    usuario = db.session.get(Usuario, usuario_id)
    if usuario is None:
        abort(404)
    return usuario


# --------------------------------------------------------------------------
# Listado
# --------------------------------------------------------------------------

@usuarios_bp.route("/")
@login_required
@requiere_rol(Rol.ADMIN)
def lista():
    consulta = db.session.query(Usuario)

    rol = request.args.get("rol", "").strip()
    if rol in Rol.ETIQUETAS:
        consulta = consulta.filter(Usuario.rol == rol)
    else:
        rol = ""

    estado = request.args.get("estado", "").strip()
    if estado == "activos":
        consulta = consulta.filter(Usuario.activo.is_(True))
    elif estado == "inactivos":
        consulta = consulta.filter(Usuario.activo.is_(False))
    else:
        estado = ""

    busqueda = request.args.get("q", "").strip()
    if busqueda:
        patron = f"%{busqueda}%"
        consulta = consulta.filter(
            or_(
                Usuario.nombre.ilike(patron),
                Usuario.correo.ilike(patron),
                Usuario.documento.ilike(patron),
            )
        )

    usuarios = consulta.order_by(Usuario.rol, Usuario.nombre).all()

    conteos = dict(
        db.session.query(Usuario.rol, func.count(Usuario.id))
        .group_by(Usuario.rol)
        .all()
    )
    # Clientes que todavia no pueden entrar al portal: es la lista de pendientes
    # de habilitacion que antes solo se veia consultando la base.
    sin_portal = (
        db.session.query(func.count(Cliente.id))
        .filter(Cliente.usuario_id.is_(None), Cliente.activo.is_(True))
        .scalar()
    )

    return render_template(
        "admin/usuarios/lista.html",
        usuarios=usuarios,
        conteos=conteos,
        sin_portal=sin_portal,
        formulario=FormularioAccion(),
        filtros={"rol": rol, "estado": estado, "q": busqueda},
        roles=Rol.opciones(),
    )


# --------------------------------------------------------------------------
# Alta
# --------------------------------------------------------------------------

@usuarios_bp.route("/nuevo", methods=["GET", "POST"])
@login_required
@requiere_rol(Rol.ADMIN)
def nuevo():
    formulario = FormularioUsuario()

    # Permite llegar desde el directorio de clientes con el cliente preseleccionado.
    if request.method == "GET":
        cliente_id = request.args.get("cliente_id", type=int)
        if cliente_id and cliente_id in {v for v, _ in formulario.cliente_id.choices}:
            formulario.cliente_id.data = cliente_id
            formulario.rol.data = Rol.CLIENTE
            cliente = db.session.get(Cliente, cliente_id)
            if cliente is not None:
                formulario.nombre.data = cliente.nombre
                formulario.correo.data = cliente.correo or ""
                formulario.telefono.data = cliente.telefono or ""

    if formulario.validate_on_submit():
        try:
            usuario, generada, cliente = crear_usuario(formulario.datos(), actor=current_user)
        except DatosInvalidos as error:
            _mostrar_errores(formulario, error)
        else:
            registrar_auditoria(
                "Crear usuario", "usuario",
                f"Creó la cuenta de {usuario.nombre} ({usuario.correo}) como {usuario.rol_etiqueta}.",
                _datos_cuenta(usuario, cliente), entidad_id=usuario.id,
            )
            db.session.commit()
            flash(f"Cuenta de {usuario.nombre} creada como {usuario.rol_etiqueta}.", "exito")
            if cliente is not None:
                flash(f"Portal habilitado para {cliente.nombre}.", "exito")
            if generada:
                # Se muestra una sola vez: evita que el administrador reutilice una
                # clave conocida y no queda guardada en ningun otro lugar.
                flash(
                    f"Contrasena temporal de {usuario.correo}: {generada} — "
                    "entreguela por un canal aparte; no se volvera a mostrar.",
                    "advertencia",
                )
            return redirect(url_for("usuarios.lista"))

    return render_template(
        "admin/usuarios/formulario.html", formulario=formulario, usuario=None,
        longitud_minima=LONGITUD_MINIMA,
    )


# --------------------------------------------------------------------------
# Edicion
# --------------------------------------------------------------------------

@usuarios_bp.route("/<int:usuario_id>/editar", methods=["GET", "POST"])
@login_required
@requiere_rol(Rol.ADMIN)
def editar(usuario_id):
    usuario = _usuario(usuario_id)
    es_propia = usuario.id == current_user.id
    formulario = FormularioUsuario(obj=usuario, usuario=usuario)

    if request.method == "GET" and usuario.cliente is not None:
        formulario.cliente_id.data = usuario.cliente.id

    if formulario.validate_on_submit():
        try:
            cambios, consecuencias = editar_usuario(usuario, formulario.datos(), actor=current_user)
        except DatosInvalidos as error:
            db.session.rollback()
            _mostrar_errores(formulario, error)
        else:
            if cambios:
                registrar_auditoria(
                    "Editar usuario", "usuario",
                    f"Editó la cuenta de {usuario.nombre}: "
                    + ", ".join(campo.replace("_", " ") for campo in cambios) + ".",
                    cambios_legibles(cambios), entidad_id=usuario.id,
                )
            db.session.commit()
            flash(f"Cuenta de {usuario.nombre} actualizada.", "exito")
            for consecuencia in consecuencias:
                flash(consecuencia[0].upper() + consecuencia[1:] + ".", "advertencia")
            return redirect(url_for("usuarios.lista"))

    return render_template(
        "admin/usuarios/formulario.html", formulario=formulario, usuario=usuario,
        es_propia=es_propia, rutas_abiertas=rutas_abiertas(usuario),
        longitud_minima=LONGITUD_MINIMA,
    )


# --------------------------------------------------------------------------
# Acciones puntuales
# --------------------------------------------------------------------------

@usuarios_bp.route("/<int:usuario_id>/alternar-estado", methods=["POST"])
@login_required
@requiere_rol(Rol.ADMIN)
def alternar_estado(usuario_id):
    """Activa o desactiva la cuenta. Sustituye al borrado (ver cabecera)."""
    usuario = _usuario(usuario_id)

    if not FormularioAccion().validate_on_submit():
        flash("Solicitud invalida. Intente nuevamente.", "error")
        return redirect(url_for("usuarios.lista"))

    try:
        cambiar_estado_usuario(usuario, not usuario.activo, actor=current_user)
    except DatosInvalidos as error:
        flash(str(error), "error")
        return redirect(url_for("usuarios.lista"))

    registrar_auditoria(
        "Activar usuario" if usuario.activo else "Desactivar usuario", "usuario",
        f"{'Activó' if usuario.activo else 'Desactivó'} la cuenta de {usuario.nombre}.",
        {"correo": usuario.correo, "activo": [not usuario.activo, usuario.activo]},
        entidad_id=usuario.id,
    )
    db.session.commit()
    flash(
        f"Cuenta de {usuario.nombre} "
        + ("activada." if usuario.activo else "desactivada. No podra iniciar sesion."),
        "exito",
    )
    return redirect(url_for("usuarios.lista"))


@usuarios_bp.route("/<int:usuario_id>/restablecer", methods=["POST"])
@login_required
@requiere_rol(Rol.ADMIN)
def restablecer(usuario_id):
    """Emite una contrasena temporal nueva y la muestra una sola vez."""
    usuario = _usuario(usuario_id)

    if not FormularioAccion().validate_on_submit():
        flash("Solicitud invalida. Intente nuevamente.", "error")
        return redirect(url_for("usuarios.lista"))

    temporal = restablecer_contrasena(usuario)
    registrar_auditoria(
        "Restablecer contraseña", "usuario",
        f"Restableció la contraseña de {usuario.nombre}.",
        {"correo": usuario.correo}, entidad_id=usuario.id,
    )
    db.session.commit()

    flash(
        f"Contrasena temporal de {usuario.correo}: {temporal} — entreguela por un "
        "canal aparte; no se volvera a mostrar.",
        "advertencia",
    )
    return redirect(url_for("usuarios.lista"))
