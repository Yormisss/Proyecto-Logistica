"""Cuentas de acceso (RF1): reglas compartidas por la pantalla y la voz.

Las cuentas no se eliminan, se desactivan (ver app/controllers/usuarios.py).
Protecciones: nadie puede desactivarse ni quitarse el rol de administrador a
si mismo, y siempre queda al menos un administrador activo. El correo de
inicio de sesion y la contrasena solo los maneja el admin en pantalla; por voz
nunca se dicen ni se aceptan (el asistente no los envia a estas funciones).

Ninguna funcion hace commit.
"""

import secrets
import string

from sqlalchemy import func, or_

from app.extensions import db
from app.models import Cliente, EstadoRuta, Rol, Ruta, Usuario
from app.services.validacion import DatosInvalidos, Validador

LONGITUD_MINIMA = 8
CAMPOS_USUARIO = ("nombre", "correo", "documento", "telefono", "rol", "activo")


def generar_contrasena(longitud=12):
    """Contrasena aleatoria para una cuenta: `secrets`, porque es una credencial."""
    alfabeto = string.ascii_letters + string.digits + "!*-_"
    while True:
        contrasena = "".join(secrets.choice(alfabeto) for _ in range(longitud))
        # Con letras y numeros, para que tambien sirva como contrasena fija.
        if any(c.isalpha() for c in contrasena) and any(c.isdigit() for c in contrasena):
            return contrasena


def _booleano(valor, defecto=True):
    if valor is None or valor == "":
        return defecto
    if isinstance(valor, str):
        return valor.strip().lower() in ("1", "true", "si", "sí", "on", "y", "yes")
    return bool(valor)


def administradores_activos(excluyendo=None):
    consulta = db.session.query(func.count(Usuario.id)).filter(
        Usuario.rol == Rol.ADMIN, Usuario.activo.is_(True)
    )
    if excluyendo is not None:
        consulta = consulta.filter(Usuario.id != excluyendo)
    return consulta.scalar()


def rutas_abiertas(usuario):
    """Rutas sin cerrar de un conductor; condiciona cambiarle el rol o desactivarlo."""
    if usuario.rol != Rol.CONDUCTOR:
        return 0
    return (
        db.session.query(func.count(Ruta.id))
        .filter(Ruta.conductor_id == usuario.id,
                Ruta.estado.in_((EstadoRuta.PLANIFICADA, EstadoRuta.EN_CURSO)))
        .scalar()
    )


def clientes_disponibles(usuario=None):
    """Clientes activos sin cuenta de portal, mas el ya vinculado a `usuario`."""
    consulta = db.session.query(Cliente).filter(Cliente.activo.is_(True))
    if usuario is not None and usuario.cliente is not None:
        consulta = consulta.filter(or_(Cliente.usuario_id.is_(None), Cliente.id == usuario.cliente.id))
    else:
        consulta = consulta.filter(Cliente.usuario_id.is_(None))
    return consulta.order_by(Cliente.nombre).all()


def validar_usuario(datos, usuario=None, actor=None):
    """Datos normalizados de una cuenta; lanza DatosInvalidos si no cumplen.

    `datos` puede traer `cliente_id` (obligatorio para el rol CLIENTE) y
    `contrasena` (opcional, solo pantalla). `actor` es quien hace el cambio.
    """
    actuales = {campo: getattr(usuario, campo) for campo in CAMPOS_USUARIO} if usuario else {}
    if usuario is not None and usuario.cliente is not None:
        actuales["cliente_id"] = usuario.cliente.id
    datos = {**actuales, **datos}
    v = Validador()
    nombre = v.texto(datos, "nombre", "el nombre", 120, requerido=True)
    correo = v.correo(datos, "correo", "el correo", requerido=True)
    documento = v.texto(datos, "documento", "el documento", 30)
    telefono = v.texto(datos, "telefono", "el teléfono", 30)
    rol = (datos.get("rol") or "").strip() if isinstance(datos.get("rol"), str) else datos.get("rol")
    if not rol:
        v.falta("rol", "el rol")
    elif rol not in Rol.ETIQUETAS:
        v.invalido("rol", "El rol seleccionado no es valido.")
    activo = _booleano(datos.get("activo"))
    contrasena = datos.get("contrasena") or None
    if contrasena is not None and len(contrasena) < LONGITUD_MINIMA:
        v.invalido("contrasena", f"Minimo {LONGITUD_MINIMA} caracteres.")

    propio = usuario.id if usuario is not None else None
    if correo:
        choque = db.session.query(Usuario).filter(Usuario.correo == correo)
        if propio:
            choque = choque.filter(Usuario.id != propio)
        if choque.first():
            v.invalido("correo", "Ya existe una cuenta con este correo.")
    if documento:
        choque = db.session.query(Usuario).filter(Usuario.documento == documento)
        if propio:
            choque = choque.filter(Usuario.id != propio)
        if choque.first():
            v.invalido("documento", "Ya existe una cuenta con este documento.")

    cliente_id = None
    if rol == Rol.CLIENTE:
        try:
            cliente_id = int(datos.get("cliente_id") or 0)
        except (TypeError, ValueError):
            cliente_id = 0
        if not cliente_id:
            v.invalido("cliente_id", "Una cuenta de cliente debe apuntar al cliente cuyos pedidos vera.")
        elif cliente_id not in {c.id for c in clientes_disponibles(usuario)}:
            v.invalido("cliente_id", "El cliente seleccionado no esta disponible.")

    # Protecciones: nadie se quita el acceso a si mismo, y no se queda el
    # sistema sin administradores activos.
    if usuario is not None and actor is not None and usuario.id == actor.id:
        if rol and rol != Rol.ADMIN and usuario.rol == Rol.ADMIN:
            v.invalido(None, "No puede cambiar su propio rol de administrador.")
        if not activo:
            v.invalido(None, "No puede desactivar su propia cuenta.")
    if usuario is not None and usuario.rol == Rol.ADMIN and (rol != Rol.ADMIN or not activo) \
            and not v.hay_errores and administradores_activos(excluyendo=usuario.id) == 0:
        v.invalido(None, "Debe quedar al menos un administrador activo en el sistema.")

    v.lanzar_si_hay()
    return {"nombre": nombre, "correo": correo, "documento": documento, "telefono": telefono,
            "rol": rol, "activo": activo, "cliente_id": cliente_id, "contrasena": contrasena}


def _vincular_cliente(usuario, cliente_id):
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


def crear_usuario(datos, actor=None):
    """Crea la cuenta. Devuelve (usuario, contrasena_generada o None, cliente_vinculado).

    Sin contrasena en `datos` se genera una con `secrets`; el llamador decide
    como entregarla (en pantalla se muestra una sola vez; por voz viaja solo en
    el evento "credencial_usuario").
    """
    limpios = validar_usuario(datos, actor=actor)
    usuario = Usuario(nombre=limpios["nombre"], correo=limpios["correo"], documento=limpios["documento"],
                      telefono=limpios["telefono"], rol=limpios["rol"], activo=limpios["activo"])
    generada = None if limpios["contrasena"] else generar_contrasena()
    usuario.establecer_contrasena(limpios["contrasena"] or generada)
    db.session.add(usuario)
    db.session.flush()
    cliente = _vincular_cliente(usuario, limpios["cliente_id"])
    return usuario, generada, cliente


def editar_usuario(usuario, datos, actor=None):
    """Aplica los cambios. Devuelve ({campo: (antes, despues)}, rutas_abiertas)."""
    antes = {campo: getattr(usuario, campo) for campo in CAMPOS_USUARIO}
    antes["cliente_id"] = usuario.cliente.id if usuario.cliente is not None else None
    limpios = validar_usuario(datos, usuario, actor)
    abiertas = rutas_abiertas(usuario) if limpios["rol"] != usuario.rol else 0
    cambios = {campo: (antes[campo], limpios[campo]) for campo in (*CAMPOS_USUARIO, "cliente_id")
               if antes[campo] != limpios[campo]}
    for campo in CAMPOS_USUARIO:
        setattr(usuario, campo, limpios[campo])
    if limpios["contrasena"]:
        usuario.establecer_contrasena(limpios["contrasena"])
        cambios["contrasena"] = ("(oculta)", "(cambiada)")
    _vincular_cliente(usuario, limpios["cliente_id"])
    return cambios, abiertas


def cambiar_estado_usuario(usuario, activo, actor=None):
    """Activa o desactiva la cuenta. Devuelve las rutas abiertas que deja un conductor."""
    if usuario.activo == activo:
        raise DatosInvalidos([(None, f"La cuenta de {usuario.nombre} ya está "
                                     f"{'activa' if activo else 'inactiva'}.")])
    if not activo and actor is not None and usuario.id == actor.id:
        raise DatosInvalidos([(None, "No puede desactivar su propia cuenta.")])
    if not activo and usuario.rol == Rol.ADMIN and administradores_activos(usuario.id) == 0:
        raise DatosInvalidos([(None, "Debe quedar al menos un administrador activo en el sistema.")])
    abiertas = rutas_abiertas(usuario) if not activo else 0
    usuario.activo = activo
    return abiertas


def restablecer_contrasena(usuario):
    """Genera y asigna una contrasena nueva; la devuelve para entregarla."""
    contrasena = generar_contrasena()
    usuario.establecer_contrasena(contrasena)
    return contrasena
