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
from sqlalchemy import case, func, or_
from wtforms import BooleanField, PasswordField, SelectField, StringField
from wtforms.validators import DataRequired, EqualTo, Optional

from app.cifrado import indice_ciego
from app.controllers.auth import cambiar_contrasena_propia
from app.controllers.seguridad import (
    campo_correo, es_redireccion_segura, politica_contrasena, requiere_rol,
)
from app.extensions import db
from app.models import Cliente, Rol, Usuario
from app.models.usuario import METODO_HASH
from app.services import acceso
from app.services.auditoria import cambios_legibles, registrar_auditoria
from app.services.usuarios import (
    cambiar_estado_usuario, clientes_disponibles, crear_usuario, editar_usuario,
    rechazo_contrasena, restablecer_contrasena, rutas_abiertas,
)
from app.services.validacion import DatosInvalidos
from app.tiempo import ahora

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
    # El correo lleva ademas `campo_correo`, que pone la lista blanca en el
    # navegador (la misma que aplica el servicio).
    nombre = StringField("Nombre completo", validators=[Optional()])
    correo = campo_correo(validar=False)
    documento = StringField("Documento", validators=[Optional()])
    telefono = StringField("Teléfono", validators=[Optional()])
    # `validate_choice=False`: la comprobacion de WTForms responde en ingles.
    rol = SelectField("Rol", validate_choice=False, validators=[Optional()])
    # Solo aplica al rol CLIENTE: es la entidad comercial cuyos pedidos vera.
    cliente_id = SelectField(
        "Cliente asociado", coerce=_a_entero, validate_choice=False, validators=[Optional()],
    )
    # Solo en el alta; la edicion no toca la contrasena (ver su apartado).
    contrasena = PasswordField("Contraseña", validators=[Optional()])
    # Solo en el alta, si el administrador escribio la contrasena y la cuenta es
    # de un rol cuya contrasena administra: las demas siempre son temporales.
    exigir_cambio = BooleanField("Exigir cambio al primer inicio de sesion", default=True)
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
            "contrasena": self.contrasena.data or None, "exigir_cambio": self.exigir_cambio.data,
            "activo": self.activo.data,
        }


class FormularioAccion(FlaskForm):
    """Acciones POST sin datos propios; solo aporta el token CSRF."""


class FormularioAsignarContrasena(FlaskForm):
    contrasena = PasswordField(
        "Nueva contrasena",
        validators=[DataRequired("Indique la contrasena."), politica_contrasena],
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
        validators=[DataRequired("Indique la contrasena."), politica_contrasena],
    )
    confirmacion = PasswordField(
        "Confirmar contrasena",
        validators=[DataRequired("Repita la contrasena."),
                    EqualTo("contrasena", message="Las contrasenas no coinciden.")],
    )


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
                # El documento esta cifrado: solo se encuentra completo, por su
                # indice ciego (ver app/cifrado.py).
                Usuario.documento_indice == indice_ciego(busqueda),
            )
        )
    return consulta, rol, busqueda


def _volver(por_defecto):
    """Destino tras una accion que se lanza desde varias pantallas."""
    destino = request.form.get("volver", "")
    return destino if es_redireccion_segura(destino) else por_defecto


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
    destino = _volver(url_for("usuarios.lista"))

    if not FormularioAccion().validate_on_submit():
        flash("Solicitud invalida. Intente nuevamente.", "error")
        return redirect(destino)

    # La propia tampoco: cerraria la sesion en curso (ver `get_id`) y saltaria
    # la verificacion de la contrasena actual.
    try:
        temporal = restablecer_contrasena(usuario, actor=current_user)
    except DatosInvalidos as error:
        flash(str(error), "error")
        return redirect(destino)

    registrar_auditoria(
        "Restablecer contraseña", "usuario",
        f"Restableció la contraseña de {usuario.nombre}.",
        {"correo": usuario.correo}, entidad_id=usuario.id,
    )
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
    usuario = _usuario(usuario_id)
    if usuario.id == current_user.id:
        return _contrasena_propia(usuario)

    rechazo = rechazo_contrasena(usuario, current_user)
    if rechazo:
        flash(rechazo, "error")
        return redirect(url_for("usuarios.contrasenas"))

    formulario = FormularioAsignarContrasena()
    if formulario.validate_on_submit():
        usuario.establecer_contrasena(
            formulario.contrasena.data, temporal=formulario.exigir_cambio.data
        )
        registrar_auditoria(
            "Asignar contraseña", "usuario",
            f"Asignó una contraseña a {usuario.nombre}"
            + (" (temporal)." if usuario.debe_cambiar_contrasena else "."),
            {"correo": usuario.correo, "temporal": usuario.debe_cambiar_contrasena},
            entidad_id=usuario.id,
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
        accion=FormularioAccion(), hoy=ahora(), propia=False,
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
        hoy=ahora(), propia=True,
    )


@usuarios_bp.route("/<int:usuario_id>/alternar-exigencia", methods=["POST"])
@login_required
@requiere_rol(Rol.ADMIN)
def alternar_exigencia(usuario_id):
    """Marca o desmarca que la cuenta debe cambiar su contrasena."""
    usuario = _usuario(usuario_id)
    destino = _volver(url_for("usuarios.contrasenas"))

    if not FormularioAccion().validate_on_submit():
        flash("Solicitud invalida. Intente nuevamente.", "error")
        return redirect(destino)

    rechazo = rechazo_contrasena(usuario, current_user)
    if rechazo:
        flash(rechazo, "error")
        return redirect(destino)

    usuario.debe_cambiar_contrasena = not usuario.debe_cambiar_contrasena
    registrar_auditoria(
        "Exigir cambio de contraseña" if usuario.debe_cambiar_contrasena
        else "Quitar exigencia de cambio de contraseña", "usuario",
        f"{'Exigió' if usuario.debe_cambiar_contrasena else 'Quitó la exigencia de'} "
        f"cambio de contraseña a {usuario.nombre}.",
        {"correo": usuario.correo}, entidad_id=usuario.id,
    )
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
    if marcadas:
        registrar_auditoria(
            "Exigir cambio de contraseña", "usuario",
            f"Exigió el cambio de contraseña a {marcadas} cuenta(s).",
            {"cuentas": marcadas},
        )
    db.session.commit()

    flash(f"{marcadas} cuenta(s) deberan cambiar su contrasena.", "exito")
    if len(administrables) < len(ids):
        flash("Se omitieron las cuentas de clientes y administradores: solo ellos "
              "cambian la suya.", "advertencia")
    return redirect(destino)
