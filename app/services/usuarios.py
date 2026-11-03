"""Cuentas de acceso (RF1): reglas compartidas por la pantalla y la voz.

Las cuentas no se eliminan, se desactivan (ver app/controllers/usuarios.py).
Protecciones: nadie puede desactivarse ni quitarse el rol de administrador a
si mismo, y siempre queda al menos un administrador activo. El correo de
inicio de sesion y la contrasena solo los maneja el admin en pantalla; por voz
nunca se dicen ni se aceptan (el asistente no los envia a estas funciones).

Contrasenas (RNF5, iguales en pantalla y por voz): toda contrasena escrita
cumple `error_politica` (largo y clases de caracteres), y la que asigna o
genera el administrador es temporal y se debe reemplazar al iniciar sesion. Solo
administra las de gestores y conductores (`rechazo_contrasena`); el cliente y
cada administrador cambian la suya, con la actual. Editar una cuenta no toca
la contrasena: tiene su propio apartado (Usuarios > Contrasenas).

Cambio de rol y desactivacion (iguales en pantalla y por voz):
- Un conductor con rutas sin cerrar (PLANIFICADA o EN_CURSO) no cambia de rol
  ni se desactiva: la ruta quedaria sin quien la cierre.
- Un conductor que deja de serlo libera su vehiculo.
- Una cuenta de cliente no pasa a un rol interno ni al reves: son personas
  distintas (una externa y otra interna), y se crea otra cuenta. Si cambia a
  otro cliente disponible, el anterior queda sin acceso al portal.

Ninguna funcion hace commit.
"""

import secrets
import string

from sqlalchemy import func, or_

from app.cifrado import indice_ciego
from app.extensions import db
from app.models import Cliente, EstadoRuta, Rol, Ruta, Usuario
from app.services.validacion import DatosInvalidos, Validador

LONGITUD_MINIMA = 8
CAMPOS_USUARIO = ("nombre", "correo", "documento", "telefono", "rol", "activo")

# Politica de contrasenas: ademas del largo, al menos un caracter de cada
# clase. Asi no pasan secuencias como "123456789101112131415" o "abcdefgh".
CLASES_CONTRASENA = (
    (str.isupper, "una mayúscula"),
    (str.islower, "una minúscula"),
    (str.isdigit, "un número"),
    (lambda c: not c.isalnum(), "un símbolo"),
)
POLITICA_CONTRASENA = (f"Mínimo {LONGITUD_MINIMA} caracteres, con al menos una mayúscula, "
                       "una minúscula, un número y un símbolo.")


def error_politica(contrasena):
    """Lo que le falta a `contrasena` para cumplir la politica, o None si cumple."""
    contrasena = contrasena or ""
    faltan = [nombre for cumple, nombre in CLASES_CONTRASENA
              if not any(cumple(c) for c in contrasena)]
    partes = []
    if len(contrasena) < LONGITUD_MINIMA:
        partes.append(f"mínimo {LONGITUD_MINIMA} caracteres")
    if faltan:
        partes.append("al menos " + (", ".join(faltan[:-1]) + " y " + faltan[-1]
                                     if len(faltan) > 1 else faltan[0]))
    if not partes:
        return None
    return "La contraseña debe tener " + " y ".join(partes) + "."


def generar_contrasena(longitud=12):
    """Contrasena aleatoria para una cuenta: `secrets`, porque es una credencial."""
    alfabeto = string.ascii_letters + string.digits + "!*-_"
    while True:
        contrasena = "".join(secrets.choice(alfabeto) for _ in range(longitud))
        # Que cumpla la politica, para que tambien sirva como contrasena fija.
        if error_politica(contrasena) is None:
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


def _rutas_sin_cerrar(usuario, abiertas):
    return (f"{usuario.nombre} tiene {abiertas} {'ruta' if abiertas == 1 else 'rutas'} sin cerrar; "
            "elimínelas si no han salido o espere a que se cierren.")


CAMBIO_CLIENTE_INTERNO = ("Las cuentas de cliente no cambian a un rol interno ni al revés; "
                          "cree una cuenta nueva.")


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
    if contrasena is not None and (error := error_politica(contrasena)):
        v.invalido("contrasena", error)

    propio = usuario.id if usuario is not None else None
    if correo:
        choque = db.session.query(Usuario).filter(Usuario.correo == correo)
        if propio:
            choque = choque.filter(Usuario.id != propio)
        if choque.first():
            v.invalido("correo", "Ya existe una cuenta con este correo.")
    if documento:
        # El documento esta cifrado: se compara su indice ciego.
        choque = db.session.query(Usuario).filter(Usuario.documento_indice == indice_ciego(documento))
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

    if usuario is not None and rol in Rol.ETIQUETAS and rol != usuario.rol:
        if (rol == Rol.CLIENTE) != (usuario.rol == Rol.CLIENTE):
            v.invalido("rol", CAMBIO_CLIENTE_INTERNO)
        elif usuario.rol == Rol.CONDUCTOR and (abiertas := rutas_abiertas(usuario)):
            v.invalido("rol", _rutas_sin_cerrar(usuario, abiertas))
    if usuario is not None and usuario.activo and not activo and usuario.rol == Rol.CONDUCTOR \
            and (abiertas := rutas_abiertas(usuario)):
        v.invalido(None, _rutas_sin_cerrar(usuario, abiertas))

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
    # La escrita por el administrador solo es definitiva si el lo pide
    # (`exigir_cambio` falso) y la cuenta es de un rol cuya contrasena administra.
    usuario.establecer_contrasena(
        limpios["contrasena"] or generada,
        temporal=(generada is not None
                  or _booleano(datos.get("exigir_cambio"))
                  or not usuario.contrasena_administrable),
    )
    db.session.add(usuario)
    db.session.flush()
    cliente = _vincular_cliente(usuario, limpios["cliente_id"])
    return usuario, generada, cliente


def consecuencias_edicion(usuario, limpios):
    """Frases con lo que arrastra un cambio ya validado, para avisarlo antes.

    "el vehiculo ABC123 queda sin conductor", "Tienda Sur queda sin acceso al
    portal". La voz las lee en el resumen y la pantalla las muestra al guardar.
    """
    frases = []
    if usuario.rol == Rol.CONDUCTOR and limpios["rol"] != Rol.CONDUCTOR and usuario.vehiculo is not None:
        frases.append(f"el vehículo {usuario.vehiculo.placa} queda sin conductor")
    anterior = usuario.cliente
    if anterior is not None and limpios["rol"] == Rol.CLIENTE and limpios["cliente_id"] != anterior.id:
        frases.append(f"{anterior.nombre} queda sin acceso al portal")
    return frases


def editar_usuario(usuario, datos, actor=None):
    """Aplica los cambios. Devuelve ({campo: (antes, despues)}, consecuencias)."""
    antes = {campo: getattr(usuario, campo) for campo in CAMPOS_USUARIO}
    antes["cliente_id"] = usuario.cliente.id if usuario.cliente is not None else None
    limpios = validar_usuario(datos, usuario, actor)
    consecuencias = consecuencias_edicion(usuario, limpios)
    cambios = {campo: (antes[campo], limpios[campo]) for campo in (*CAMPOS_USUARIO, "cliente_id")
               if antes[campo] != limpios[campo]}
    if usuario.rol == Rol.CONDUCTOR and limpios["rol"] != Rol.CONDUCTOR and usuario.vehiculo is not None:
        cambios["vehiculo"] = (usuario.vehiculo.placa, None)
        usuario.vehiculo.conductor_id = None
    for campo in CAMPOS_USUARIO:
        setattr(usuario, campo, limpios[campo])
    # La contrasena no se cambia aqui aunque venga en `datos`: tiene su propio
    # apartado, con las reglas de `rechazo_contrasena`.
    _vincular_cliente(usuario, limpios["cliente_id"])
    return cambios, consecuencias


def verificar_cambio_estado(usuario, activo, actor=None):
    """Lanza DatosInvalidos si la cuenta no puede quedar `activo`. No cambia nada."""
    if usuario.activo == activo:
        raise DatosInvalidos([(None, f"La cuenta de {usuario.nombre} ya está "
                                     f"{'activa' if activo else 'inactiva'}.")])
    if not activo and actor is not None and usuario.id == actor.id:
        raise DatosInvalidos([(None, "No puede desactivar su propia cuenta.")])
    if not activo and usuario.rol == Rol.ADMIN and administradores_activos(usuario.id) == 0:
        raise DatosInvalidos([(None, "Debe quedar al menos un administrador activo en el sistema.")])
    if not activo and (abiertas := rutas_abiertas(usuario)):
        raise DatosInvalidos([(None, _rutas_sin_cerrar(usuario, abiertas))])


def cambiar_estado_usuario(usuario, activo, actor=None):
    """Activa o desactiva la cuenta (ver `verificar_cambio_estado`)."""
    verificar_cambio_estado(usuario, activo, actor)
    usuario.activo = activo


def rechazo_contrasena(usuario, actor=None):
    """Por que el administrador no puede gestionar esta contrasena, o None.

    El cliente es el unico que cambia la suya, y la de un administrador solo
    la cambia el mismo, con la actual: asi ninguna cuenta con acceso total
    puede apropiarse de otra.
    """
    if actor is not None and usuario.id == actor.id:
        return "Su propia contrasena se cambia en Contrasenas, con la actual."
    if usuario.rol == Rol.CLIENTE:
        return "Solo el cliente puede cambiar su propia contrasena."
    if not usuario.contrasena_administrable:
        return "Solo cada administrador puede cambiar su propia contrasena."
    return None


def restablecer_contrasena(usuario, actor=None):
    """Genera una contrasena temporal nueva y la devuelve para entregarla.

    Lanza DatosInvalidos si la cuenta no es de las que administra el
    administrador (ver `rechazo_contrasena`). Cierra las sesiones abiertas.
    """
    rechazo = rechazo_contrasena(usuario, actor)
    if rechazo:
        raise DatosInvalidos([(None, rechazo)])
    contrasena = generar_contrasena()
    usuario.establecer_contrasena(contrasena, temporal=True)
    return contrasena


def verificar_exigencia(usuario, exigir, actor=None):
    """Lanza DatosInvalidos si no se puede marcar (o quitar) el cambio de contrasena."""
    rechazo = rechazo_contrasena(usuario, actor)
    if rechazo:
        raise DatosInvalidos([(None, rechazo)])
    if bool(usuario.debe_cambiar_contrasena) == bool(exigir):
        raise DatosInvalidos([(None, (f"{usuario.nombre} ya debe cambiar su contraseña." if exigir
                                      else f"{usuario.nombre} no tiene pendiente cambiar su contraseña."))])


def cambiar_exigencia(usuario, exigir, actor=None):
    """Marca (o quita) que la cuenta debe cambiar su contrasena al entrar.

    Mismas cuentas que `restablecer_contrasena`: gestores y conductores. No
    toca el hash: la persona entra con la que ya conoce y el sistema le pide
    reemplazarla, tambien en las sesiones ya abiertas (y en su llamada de voz).
    """
    verificar_exigencia(usuario, exigir, actor)
    usuario.debe_cambiar_contrasena = bool(exigir)


def cuentas_contrasena_administrada(ids=None, rol=None):
    """Ids de las cuentas cuya contrasena administra el admin (gestores y
    conductores), entre `ids` si se dan y del `rol` si se da."""
    consulta = db.session.query(Usuario.id).filter(Usuario.rol.in_(Rol.CONTRASENA_ADMINISTRADA))
    if ids is not None:
        consulta = consulta.filter(Usuario.id.in_(list(ids)))
    if rol is not None:
        consulta = consulta.filter(Usuario.rol == rol)
    return [id_ for (id_,) in consulta]


def exigir_cambio_cuentas(ids):
    """Obliga a las cuentas `ids` a renovar su contrasena (p. ej. ante una fuga).

    Solo alcanza a gestores y conductores aunque `ids` traiga otras cuentas.
    Devuelve cuantas quedaron marcadas (las que ya lo estaban no cuentan).
    """
    return (
        db.session.query(Usuario)
        .filter(Usuario.id.in_(list(ids)), Usuario.debe_cambiar_contrasena.is_(False))
        .update({Usuario.debe_cambiar_contrasena: True}, synchronize_session=False)
    )
