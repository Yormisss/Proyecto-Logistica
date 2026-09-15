"""RF1 - Administracion de cuentas de acceso.

Cierra el circuito del RF1: hasta ahora los usuarios solo podian nacer en
`seed.py`, de modo que dar de alta a un conductor o habilitar el portal de un
cliente exigia entrar a la base a mano.

Las cuentas **no se eliminan**, se desactivan. `Usuario` es el origen de las
claves ajenas de `rutas`, `eventos_pedido` y `movimientos_inventario`: borrar una
cuenta romperia la trazabilidad que sostiene el RF6. El login ya rechaza a los
usuarios inactivos, asi que desactivar es el equivalente funcional de eliminar
sin perder el historico.
"""

import secrets
import string

from flask import (
    Blueprint, abort, flash, redirect, render_template, request, url_for,
)
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from sqlalchemy import func, or_
from wtforms import BooleanField, PasswordField, SelectField, StringField
from wtforms.validators import DataRequired, Email, Length, Optional

from app.controllers.seguridad import requiere_rol
from app.extensions import db
from app.models import Cliente, EstadoRuta, Rol, Ruta, Usuario

usuarios_bp = Blueprint("usuarios", __name__)

LONGITUD_MINIMA = 8
SIN_CLIENTE = 0


def generar_contrasena(longitud=12):
    """Contrasena temporal para una cuenta nueva.

    Se usa `secrets` y no `random`: este valor es una credencial, no un dato de
    demostracion. Se muestra una sola vez al administrador para que la entregue
    por un canal aparte; el usuario la cambia desde su perfil.
    """
    alfabeto = string.ascii_letters + string.digits + "!*-_"
    return "".join(secrets.choice(alfabeto) for _ in range(longitud))


def _a_entero(valor):
    """Coercion tolerante del select de clientes.

    Un valor no numerico se interpreta como "sin cliente": asi el fallo lo
    reporta `_validar_cliente` con un mensaje en espanol, en lugar del error de
    coercion de WTForms.
    """
    try:
        return int(valor)
    except (TypeError, ValueError):
        return SIN_CLIENTE


class FormularioUsuario(FlaskForm):
    nombre = StringField(
        "Nombre completo",
        validators=[DataRequired("Indique el nombre."), Length(max=120)],
    )
    correo = StringField(
        "Correo electronico",
        validators=[DataRequired("Indique el correo."), Email("Correo no valido."), Length(max=120)],
    )
    documento = StringField("Documento", validators=[Optional(), Length(max=30)])
    telefono = StringField("Telefono", validators=[Optional(), Length(max=30)])
    # `validate_choice=False` en ambos selects: la comprobacion de opciones de
    # WTForms responde "Not a valid choice." en ingles, y el resto de la interfaz
    # esta en espanol. Se valida en `_validar_rol` y `_validar_cliente`.
    rol = SelectField("Rol", validate_choice=False, validators=[DataRequired()])
    # Solo aplica al rol CLIENTE: es la entidad comercial cuyos pedidos vera.
    cliente_id = SelectField(
        "Cliente asociado", coerce=_a_entero, validate_choice=False,
        validators=[Optional()],
    )
    contrasena = PasswordField(
        "Contrasena",
        validators=[Optional(), Length(min=LONGITUD_MINIMA,
                                      message=f"Minimo {LONGITUD_MINIMA} caracteres.")],
    )
    activo = BooleanField("Cuenta activa", default=True)

    def __init__(self, *args, usuario=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.rol.choices = Rol.opciones()
        self.cliente_id.choices = _opciones_cliente(usuario)


class FormularioAccion(FlaskForm):
    """Acciones POST sin datos propios; solo aporta el token CSRF."""


def _opciones_cliente(usuario=None):
    """Clientes que pueden recibir una cuenta de portal.

    Se ofrecen los que aun no tienen cuenta, mas el que ya esta vinculado al
    usuario en edicion: la relacion es 1 a 0..1 y la columna es unica, asi que un
    cliente no puede quedar asociado a dos cuentas.
    """
    consulta = db.session.query(Cliente).filter(Cliente.activo.is_(True))

    if usuario is not None and usuario.cliente is not None:
        consulta = consulta.filter(
            or_(Cliente.usuario_id.is_(None), Cliente.id == usuario.cliente.id)
        )
    else:
        consulta = consulta.filter(Cliente.usuario_id.is_(None))

    opciones = [(SIN_CLIENTE, "— Seleccione el cliente —")]
    opciones += [
        (c.id, f"{c.nombre}" + (f" · NIT {c.documento}" if c.documento else ""))
        for c in consulta.order_by(Cliente.nombre).all()
    ]
    return opciones


def _administradores_activos(excluyendo=None):
    consulta = db.session.query(func.count(Usuario.id)).filter(
        Usuario.rol == Rol.ADMIN, Usuario.activo.is_(True)
    )
    if excluyendo is not None:
        consulta = consulta.filter(Usuario.id != excluyendo)
    return consulta.scalar()


def _validar_unicidad(formulario, usuario=None):
    """Correo y documento son unicos en el esquema; se avisa en el campo."""
    propio = usuario.id if usuario else None
    correo = formulario.correo.data.strip().lower()

    choque = db.session.query(Usuario).filter(Usuario.correo == correo)
    if propio:
        choque = choque.filter(Usuario.id != propio)
    if choque.first():
        formulario.correo.errors = list(formulario.correo.errors) + [
            "Ya existe una cuenta con este correo."
        ]

    documento = (formulario.documento.data or "").strip()
    if documento:
        choque = db.session.query(Usuario).filter(Usuario.documento == documento)
        if propio:
            choque = choque.filter(Usuario.id != propio)
        if choque.first():
            formulario.documento.errors = list(formulario.documento.errors) + [
                "Ya existe una cuenta con este documento."
            ]

    return not (formulario.correo.errors or formulario.documento.errors)


def _validar_rol(formulario):
    """Un rol fuera del catalogo solo llega en un POST fabricado a mano."""
    if formulario.rol.data not in Rol.ETIQUETAS:
        formulario.rol.errors = list(formulario.rol.errors) + [
            "El rol seleccionado no es valido."
        ]
        return False
    return True


def _validar_cliente(formulario):
    """El rol CLIENTE exige un cliente comercial; los demas roles no lo admiten."""
    if formulario.rol.data != Rol.CLIENTE:
        return True

    if formulario.cliente_id.data in (None, SIN_CLIENTE):
        formulario.cliente_id.errors = list(formulario.cliente_id.errors) + [
            "Una cuenta de cliente debe apuntar al cliente cuyos pedidos vera."
        ]
        return False

    # El select ya limita las opciones, pero la comprobacion se repite aqui:
    # un POST fabricado a mano no pasa por el formulario renderizado.
    permitidos = {valor for valor, _ in formulario.cliente_id.choices}
    if formulario.cliente_id.data not in permitidos:
        formulario.cliente_id.errors = list(formulario.cliente_id.errors) + [
            "El cliente seleccionado no esta disponible."
        ]
        return False

    return True


def _aplicar_vinculo_cliente(usuario, cliente_id):
    """Sincroniza `clientes.usuario_id` con el rol de la cuenta.

    Al dejar de ser CLIENTE la cuenta se desvincula, para que no quede una
    credencial con acceso al portal de un cliente al que ya no representa.
    """
    anterior = usuario.cliente

    if usuario.rol != Rol.CLIENTE:
        if anterior is not None:
            anterior.usuario_id = None
        return None

    nuevo = db.session.get(Cliente, cliente_id)
    if anterior is not None and anterior is not nuevo:
        anterior.usuario_id = None
    if nuevo is not None:
        nuevo.usuario_id = usuario.id
    return nuevo


def _rutas_abiertas(usuario):
    """Rutas sin cerrar de un conductor; condiciona cambiarle el rol."""
    if usuario.rol != Rol.CONDUCTOR:
        return 0
    return (
        db.session.query(func.count(Ruta.id))
        .filter(
            Ruta.conductor_id == usuario.id,
            Ruta.estado.in_((EstadoRuta.PLANIFICADA, EstadoRuta.EN_CURSO)),
        )
        .scalar()
    )


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
        valido = (
            _validar_unicidad(formulario)
            and _validar_rol(formulario)
            and _validar_cliente(formulario)
        )

        if valido:
            usuario = Usuario(
                nombre=formulario.nombre.data.strip(),
                correo=formulario.correo.data.strip().lower(),
                documento=(formulario.documento.data or "").strip() or None,
                telefono=(formulario.telefono.data or "").strip() or None,
                rol=formulario.rol.data,
                activo=formulario.activo.data,
            )

            # Sin contrasena explicita se genera una temporal y se muestra una
            # sola vez: evita que el administrador reutilice una clave conocida.
            temporal = formulario.contrasena.data or generar_contrasena()
            usuario.establecer_contrasena(temporal)

            db.session.add(usuario)
            db.session.flush()

            cliente = _aplicar_vinculo_cliente(usuario, formulario.cliente_id.data)
            db.session.commit()

            flash(f"Cuenta de {usuario.nombre} creada como {usuario.rol_etiqueta}.", "exito")
            if cliente is not None:
                flash(f"Portal habilitado para {cliente.nombre}.", "exito")
            if not formulario.contrasena.data:
                flash(
                    f"Contrasena temporal de {usuario.correo}: {temporal} — "
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
    usuario = db.session.get(Usuario, usuario_id)
    if usuario is None:
        abort(404)

    es_propia = usuario.id == current_user.id
    formulario = FormularioUsuario(obj=usuario, usuario=usuario)

    if request.method == "GET" and usuario.cliente is not None:
        formulario.cliente_id.data = usuario.cliente.id

    if formulario.validate_on_submit():
        valido = (
            _validar_unicidad(formulario, usuario)
            and _validar_rol(formulario)
            and _validar_cliente(formulario)
        )

        # Un administrador no puede quitarse a si mismo el acceso: perderia la
        # capacidad de revertirlo y dejaria el sistema sin quien gestione cuentas.
        if es_propia and formulario.rol.data != Rol.ADMIN:
            flash("No puede cambiar su propio rol de administrador.", "error")
            valido = False
        if es_propia and not formulario.activo.data:
            flash("No puede desactivar su propia cuenta.", "error")
            valido = False

        pierde_admin = usuario.rol == Rol.ADMIN and (
            formulario.rol.data != Rol.ADMIN or not formulario.activo.data
        )
        if valido and pierde_admin and _administradores_activos(excluyendo=usuario.id) == 0:
            flash("Debe quedar al menos un administrador activo en el sistema.", "error")
            valido = False

        if valido:
            rutas_abiertas = _rutas_abiertas(usuario) if formulario.rol.data != usuario.rol else 0

            usuario.nombre = formulario.nombre.data.strip()
            usuario.correo = formulario.correo.data.strip().lower()
            usuario.documento = (formulario.documento.data or "").strip() or None
            usuario.telefono = (formulario.telefono.data or "").strip() or None
            usuario.rol = formulario.rol.data
            usuario.activo = formulario.activo.data

            if formulario.contrasena.data:
                usuario.establecer_contrasena(formulario.contrasena.data)

            _aplicar_vinculo_cliente(usuario, formulario.cliente_id.data)
            db.session.commit()

            flash(f"Cuenta de {usuario.nombre} actualizada.", "exito")
            if rutas_abiertas:
                flash(
                    f"Atencion: {rutas_abiertas} ruta(s) sin cerrar siguen asignadas a "
                    f"{usuario.nombre}. Reasignelas antes de continuar la operacion.",
                    "advertencia",
                )
            return redirect(url_for("usuarios.lista"))

    return render_template(
        "admin/usuarios/formulario.html", formulario=formulario, usuario=usuario,
        es_propia=es_propia, rutas_abiertas=_rutas_abiertas(usuario),
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
    usuario = db.session.get(Usuario, usuario_id)
    if usuario is None:
        abort(404)

    if not FormularioAccion().validate_on_submit():
        flash("Solicitud invalida. Intente nuevamente.", "error")
        return redirect(url_for("usuarios.lista"))

    if usuario.id == current_user.id:
        flash("No puede desactivar su propia cuenta.", "error")
        return redirect(url_for("usuarios.lista"))

    if usuario.activo and usuario.rol == Rol.ADMIN and _administradores_activos(usuario.id) == 0:
        flash("Debe quedar al menos un administrador activo en el sistema.", "error")
        return redirect(url_for("usuarios.lista"))

    abiertas = _rutas_abiertas(usuario) if usuario.activo else 0

    usuario.activo = not usuario.activo
    db.session.commit()

    flash(
        f"Cuenta de {usuario.nombre} "
        + ("activada." if usuario.activo else "desactivada. No podra iniciar sesion."),
        "exito",
    )
    if abiertas:
        flash(
            f"{usuario.nombre} tenia {abiertas} ruta(s) sin cerrar; reasignelas.",
            "advertencia",
        )
    return redirect(url_for("usuarios.lista"))


@usuarios_bp.route("/<int:usuario_id>/restablecer", methods=["POST"])
@login_required
@requiere_rol(Rol.ADMIN)
def restablecer(usuario_id):
    """Emite una contrasena temporal nueva y la muestra una sola vez."""
    usuario = db.session.get(Usuario, usuario_id)
    if usuario is None:
        abort(404)

    if not FormularioAccion().validate_on_submit():
        flash("Solicitud invalida. Intente nuevamente.", "error")
        return redirect(url_for("usuarios.lista"))

    temporal = generar_contrasena()
    usuario.establecer_contrasena(temporal)
    db.session.commit()

    flash(
        f"Contrasena temporal de {usuario.correo}: {temporal} — entreguela por un "
        "canal aparte; no se volvera a mostrar.",
        "advertencia",
    )
    return redirect(url_for("usuarios.lista"))
