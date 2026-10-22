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
from sqlalchemy import case, func, or_
from wtforms import BooleanField, PasswordField, SelectField, StringField
from wtforms.validators import DataRequired, EqualTo, Length, Optional

from app.controllers.auth import cambiar_contrasena_propia
from app.controllers.seguridad import campo_correo, es_redireccion_segura, requiere_rol
from app.extensions import db
from app.models import Cliente, EstadoRuta, Rol, Ruta, Usuario
from app.models.usuario import METODO_HASH
from app.services import acceso
from app.tiempo import ahora

usuarios_bp = Blueprint("usuarios", __name__)

LONGITUD_MINIMA = 8
SIN_CLIENTE = 0


def generar_contrasena(longitud=12):
    """Contrasena temporal para una cuenta nueva.

    Se usa `secrets` y no `random`: este valor es una credencial, no un dato de
    demostracion. Se muestra una sola vez al administrador para que la entregue
    por un canal aparte; el usuario debe cambiarla al iniciar sesion.
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
    correo = campo_correo(obligatorio="Indique el correo.")
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
    # Solo en el alta, si el administrador escribio la contrasena y la cuenta es
    # de un rol cuya contrasena administra: las demas siempre son temporales.
    exigir_cambio = BooleanField("Exigir cambio al primer inicio de sesion", default=True)
    activo = BooleanField("Cuenta activa", default=True)

    def __init__(self, *args, usuario=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.rol.choices = Rol.opciones()
        self.cliente_id.choices = _opciones_cliente(usuario)


class FormularioAccion(FlaskForm):
    """Acciones POST sin datos propios; solo aporta el token CSRF."""


class FormularioAsignarContrasena(FlaskForm):
    contrasena = PasswordField(
        "Nueva contrasena",
        validators=[DataRequired("Indique la contrasena."),
                    Length(min=LONGITUD_MINIMA,
                           message=f"Minimo {LONGITUD_MINIMA} caracteres.")],
    )
    confirmacion = PasswordField(
        "Confirmar contrasena",
        validators=[DataRequired("Repita la contrasena."),
                    EqualTo("contrasena", message="Las contrasenas no coinciden.")],
    )
    exigir_cambio = BooleanField("Exigir cambio al iniciar sesion", default=True)


class FormularioContrasenaPropia(FlaskForm):
    contrasena_actual = PasswordField(
        "Contrasena actual", validators=[DataRequired("Indique su contrasena actual.")]
    )
    contrasena = PasswordField(
        "Nueva contrasena",
        validators=[DataRequired("Indique la contrasena."),
                    Length(min=LONGITUD_MINIMA,
                           message=f"Minimo {LONGITUD_MINIMA} caracteres.")],
    )
    confirmacion = PasswordField(
        "Confirmar contrasena",
        validators=[DataRequired("Repita la contrasena."),
                    EqualTo("contrasena", message="Las contrasenas no coinciden.")],
    )


def _rechazo_contrasena(usuario):
    """Por que el administrador no puede gestionar esta contrasena, o None.

    El cliente es el unico que cambia la suya, y la de un administrador solo
    la cambia el mismo, con la actual: asi ninguna cuenta con acceso total
    puede apropiarse de otra.
    """
    if usuario.id == current_user.id:
        return "Su propia contrasena se cambia en Contrasenas, con la actual."
    if usuario.rol == Rol.CLIENTE:
        return "Solo el cliente puede cambiar su propia contrasena."
    if not usuario.contrasena_administrable:
        return "Solo cada administrador puede cambiar su propia contrasena."
    return None


def _filtrar_rol_y_texto(consulta):
    """Filtros comunes del listado de cuentas y del apartado de contrasenas."""
    rol = request.args.get("rol", "").strip()
    if rol in Rol.ETIQUETAS:
        consulta = consulta.filter(Usuario.rol == rol)
    else:
        rol = ""

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
    return consulta, rol, busqueda


def _volver(por_defecto):
    """Destino tras una accion que se lanza desde varias pantallas."""
    destino = request.form.get("volver", "")
    return destino if es_redireccion_segura(destino) else por_defecto


def _usuario_o_404(usuario_id):
    usuario = db.session.get(Usuario, usuario_id)
    if usuario is None:
        abort(404)
    return usuario


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
    consulta, rol, busqueda = _filtrar_rol_y_texto(db.session.query(Usuario))

    estado = request.args.get("estado", "").strip()
    if estado == "activos":
        consulta = consulta.filter(Usuario.activo.is_(True))
    elif estado == "inactivos":
        consulta = consulta.filter(Usuario.activo.is_(False))
    else:
        estado = ""

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
            usuario.establecer_contrasena(
                temporal,
                temporal=(
                    not formulario.contrasena.data
                    or formulario.exigir_cambio.data
                    or not usuario.contrasena_administrable
                ),
            )

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
    usuario = _usuario_o_404(usuario_id)
    destino = _volver(url_for("usuarios.lista"))

    if not FormularioAccion().validate_on_submit():
        flash("Solicitud invalida. Intente nuevamente.", "error")
        return redirect(destino)

    # La propia tampoco: cerraria la sesion en curso (ver `get_id`) y saltaria
    # la verificacion de la contrasena actual.
    rechazo = _rechazo_contrasena(usuario)
    if rechazo:
        flash(rechazo, "error")
        return redirect(destino)

    temporal = generar_contrasena()
    usuario.establecer_contrasena(temporal, temporal=True)
    db.session.commit()

    flash(
        f"Contrasena temporal de {usuario.correo}: {temporal} — entreguela por un "
        "canal aparte; no se volvera a mostrar. Sus sesiones abiertas se cerraron "
        "y debera cambiarla al iniciar sesion.",
        "advertencia",
    )
    return redirect(destino)


# --------------------------------------------------------------------------
# Apartado de contrasenas
# --------------------------------------------------------------------------

@usuarios_bp.route("/contrasenas")
@login_required
@requiere_rol(Rol.ADMIN)
def contrasenas():
    """Estado de la contrasena de cada cuenta. Nunca la contrasena misma.

    Solo se guarda el hash (RNF5), asi que no hay nada que mostrar: lo que el
    administrador gestiona es cuando cambio, si debe cambiarla y con que
    algoritmo esta protegida.
    """
    consulta, rol, busqueda = _filtrar_rol_y_texto(db.session.query(Usuario))

    estado = request.args.get("estado", "").strip()
    if estado == "pendientes":
        consulta = consulta.filter(Usuario.debe_cambiar_contrasena.is_(True))
    elif estado == "vigentes":
        consulta = consulta.filter(Usuario.debe_cambiar_contrasena.is_(False))
    else:
        estado = ""

    usuarios = consulta.order_by(
        Usuario.debe_cambiar_contrasena.desc(), Usuario.rol, Usuario.nombre
    ).all()

    # COUNT(CASE ...) y no COUNT(...) FILTER: MySQL no admite FILTER.
    resumen = db.session.query(
        func.count(Usuario.id),
        func.count(case((Usuario.debe_cambiar_contrasena.is_(True), 1))),
        func.count(case((Usuario.contrasena_hash.like(METODO_HASH + "$%"), 1))),
    ).one()

    return render_template(
        "admin/usuarios/contrasenas.html",
        usuarios=usuarios,
        intentos=acceso.resumen(u.correo for u in usuarios),
        maximo_intentos=acceso.maximo_intentos(),
        total=resumen[0],
        pendientes=resumen[1],
        protegidas=resumen[2],
        metodo_hash=METODO_HASH,
        hoy=ahora(),
        formulario=FormularioAccion(),
        filtros={"rol": rol, "estado": estado, "q": busqueda},
        roles=Rol.opciones(),
    )


@usuarios_bp.route("/<int:usuario_id>/contrasena", methods=["GET", "POST"])
@login_required
@requiere_rol(Rol.ADMIN)
def contrasena(usuario_id):
    """Ficha de la contrasena de una cuenta: la propia o la de un gestor o conductor."""
    usuario = _usuario_o_404(usuario_id)
    if usuario.id == current_user.id:
        return _contrasena_propia(usuario)

    rechazo = _rechazo_contrasena(usuario)
    if rechazo:
        flash(rechazo, "error")
        return redirect(url_for("usuarios.contrasenas"))

    formulario = FormularioAsignarContrasena()
    if formulario.validate_on_submit():
        usuario.establecer_contrasena(
            formulario.contrasena.data, temporal=formulario.exigir_cambio.data
        )
        db.session.commit()

        flash(
            f"Contrasena de {usuario.nombre} actualizada. Sus sesiones abiertas se cerraron"
            + ("; debera cambiarla al iniciar sesion." if usuario.debe_cambiar_contrasena
               else "."),
            "exito",
        )
        return redirect(url_for("usuarios.contrasenas"))

    return render_template(
        "admin/usuarios/contrasena.html", usuario=usuario, formulario=formulario,
        accion=FormularioAccion(), longitud_minima=LONGITUD_MINIMA, hoy=ahora(),
        propia=False,
    )


def _contrasena_propia(usuario):
    """El administrador cambia la suya como cualquier usuario: con la actual."""
    formulario = FormularioContrasenaPropia()
    if formulario.validate_on_submit():
        error = cambiar_contrasena_propia(
            usuario, formulario.contrasena_actual.data, formulario.contrasena.data
        )
        if error:
            flash(error, "error")
        else:
            flash("Su contrasena fue actualizada. Sus otras sesiones se cerraron.", "exito")
            return redirect(url_for("usuarios.contrasenas"))

    return render_template(
        "admin/usuarios/contrasena.html", usuario=usuario, formulario=formulario,
        longitud_minima=LONGITUD_MINIMA, hoy=ahora(), propia=True,
    )


@usuarios_bp.route("/<int:usuario_id>/alternar-exigencia", methods=["POST"])
@login_required
@requiere_rol(Rol.ADMIN)
def alternar_exigencia(usuario_id):
    """Marca o desmarca que la cuenta debe cambiar su contrasena."""
    usuario = _usuario_o_404(usuario_id)
    destino = _volver(url_for("usuarios.contrasenas"))

    if not FormularioAccion().validate_on_submit():
        flash("Solicitud invalida. Intente nuevamente.", "error")
        return redirect(destino)

    rechazo = _rechazo_contrasena(usuario)
    if rechazo:
        flash(rechazo, "error")
        return redirect(destino)

    usuario.debe_cambiar_contrasena = not usuario.debe_cambiar_contrasena
    db.session.commit()

    flash(
        f"{usuario.nombre} "
        + ("debera cambiar su contrasena en su proxima peticion."
           if usuario.debe_cambiar_contrasena else "ya no esta obligado a cambiar su contrasena."),
        "exito",
    )
    return redirect(destino)


@usuarios_bp.route("/contrasenas/exigir-cambio", methods=["POST"])
@login_required
@requiere_rol(Rol.ADMIN)
def exigir_cambio_masivo():
    """Obliga a varias cuentas a renovar su contrasena (p. ej. ante una fuga).

    No toca el hash: cada persona entra con la contrasena que ya conoce y el
    sistema le pide reemplazarla. Las sesiones abiertas quedan retenidas en el
    perfil desde su siguiente peticion. Solo alcanza a gestores y conductores,
    aunque el POST traiga otras cuentas.
    """
    destino = url_for("usuarios.contrasenas")
    if not FormularioAccion().validate_on_submit():
        flash("Solicitud invalida. Intente nuevamente.", "error")
        return redirect(destino)

    ids = set(request.form.getlist("usuarios", type=int))
    administrables = [
        id_ for (id_,) in db.session.query(Usuario.id).filter(
            Usuario.id.in_(ids), Usuario.rol.in_(Rol.CONTRASENA_ADMINISTRADA)
        )
    ]
    if not administrables:
        flash("Seleccione al menos una cuenta de gestor logistico o conductor.", "error")
        return redirect(destino)

    marcadas = (
        db.session.query(Usuario)
        .filter(Usuario.id.in_(administrables), Usuario.debe_cambiar_contrasena.is_(False))
        .update({Usuario.debe_cambiar_contrasena: True}, synchronize_session=False)
    )
    db.session.commit()

    flash(f"{marcadas} cuenta(s) deberan cambiar su contrasena.", "exito")
    if len(administrables) < len(ids):
        flash("Se omitieron las cuentas de clientes y administradores: solo ellos "
              "cambian la suya.", "advertencia")
    return redirect(destino)
