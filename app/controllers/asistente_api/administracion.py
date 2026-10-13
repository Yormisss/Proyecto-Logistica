"""Administracion por voz (solo el admin): usuarios, configuracion y bitacora.

Usuarios: las contrasenas nunca se dicen ni se aceptan por voz. Al crear o
restablecer una cuenta, el servidor genera la contrasena y la envia solo en el
evento `credencial_usuario`, por su propio webhook, al correo del usuario. Sin
ese webhook (MAKE_WEBHOOK_CREDENCIALES_URL) esas dos operaciones se rechazan
ya en el resumen. El correo de inicio de sesion se elige al crear la cuenta y
despues solo lo cambia un administrador en pantalla. Las reglas de rol y de
desactivacion son las de `app.services.usuarios`, las mismas de la pantalla.
"""

from datetime import datetime, time, timedelta

from flask import g

from app.controllers.asistente_api import (
    cantidad, exigir_confirmacion, funcion_agrupada, funcion_asistente, responder,
)
from app.controllers.asistente_api.comun import (
    diferencias, dichos, entero, enumerar, fecha_iso, frase_datos_invalidos, leer_cambios, me_falta,
    resolver, texto, valor_leido,
)
from app.extensions import db
from app.models import Auditoria, Rol, Usuario, normalizar_texto
from app.services.busqueda_voz import buscar_clientes, buscar_motivos, buscar_usuarios
from app.services.configuracion import (
    agregar_motivo, cambiar_estado_motivo, catalogo, centro_distribucion, editar_centro, renombrar_motivo,
    validar_agregar_motivo, validar_cambio_estado_motivo, validar_centro, validar_renombrar_motivo,
)
from app.services.notificaciones import (
    ACCION_CREACION, ACCION_RESTABLECIMIENTO, aviso_credencial_usuario, credenciales_configuradas,
    etiqueta_rol,
)
from app.services.usuarios import (
    cambiar_estado_usuario, clientes_disponibles, consecuencias_edicion, crear_usuario, editar_usuario,
    restablecer_contrasena, rutas_abiertas, validar_usuario, verificar_cambio_estado,
)
from app.services.validacion import DatosInvalidos
from app.tiempo import hoy

ESPACIO = "admin"
ROLES = (Rol.ADMIN,)

SIN_CREDENCIALES = (
    "No puedo {} por voz: falta configurar el envío de contraseñas por correo "
    "(MAKE_WEBHOOK_CREDENCIALES_URL). Hazlo en pantalla, en Usuarios."
)
AVISO_CORREO = ("La contraseña se genera sola y le llega solo a ese correo; si no le llega, "
                "restablécela en pantalla.")

# Como se dice cada rol -> constante.
ROLES_DICHOS = {
    "conductor": Rol.CONDUCTOR, "gestor": Rol.DESPACHADOR, "gestor logistico": Rol.DESPACHADOR,
    "despachador": Rol.DESPACHADOR, "administrador": Rol.ADMIN, "admin": Rol.ADMIN,
    "cliente": Rol.CLIENTE,
}


def _no_se_pudo(error):
    db.session.rollback()
    return responder(frase_datos_invalidos(error))


# --------------------------------------------------------------------------
# Usuarios
# --------------------------------------------------------------------------

ETIQUETAS_USUARIO = {"nombre": "el nombre", "documento": "el documento", "telefono": "el teléfono",
                     "rol": "el rol", "cliente": "el cliente del portal"}

usuarios = funcion_agrupada(
    ESPACIO, "usuarios", roles=ROLES, entidad="usuario",
    descripcion="Cuentas de acceso. Las contrasenas nunca se dicen por voz: al crear o restablecer, "
                "llegan al correo del usuario. El correo de una cuenta existente solo se cambia en "
                "pantalla.",
    parametros={
        "usuario": {"type": "string",
                    "description": "consultar, editar, activar, desactivar, restablecer-contrasena: "
                                   "nombre o correo de la cuenta. En consultar, sin usuario da el resumen."},
        "nombre": {"type": "string", "description": "crear y editar: nombre completo."},
        "correo": {"type": "string",
                   "description": "crear: correo de inicio de sesion, deletreado y confirmado."},
        "rol": {"type": "string", "enum": ["conductor", "gestor", "administrador", "cliente"],
                "description": "crear y editar: rol de la cuenta."},
        "cliente": {"type": "string",
                    "description": "crear y editar con rol cliente: cliente cuyo portal vera (sin cuenta aun)."},
        "documento": {"type": "string", "description": "crear y editar: documento (opcional)."},
        "telefono": {"type": "string", "description": "crear y editar: telefono (opcional)."},
    },
)


def _usuario():
    buscado = texto("usuario")
    if not buscado:
        return None, me_falta(["la cuenta (su nombre o correo)"])
    return resolver(buscar_usuarios(buscado), lambda u: f"{u.nombre}, {etiqueta_rol(u.rol)}", "cuentas",
                    f"No encontré una cuenta para {buscado}.")


def _rol_dicho():
    """(rol, error); (None, None) si no se dijo."""
    dicho = normalizar_texto(texto("rol"))
    if not dicho:
        return None, None
    rol = ROLES_DICHOS.get(dicho)
    if rol is None:
        return None, "El rol debe ser conductor, gestor logístico, administrador o cliente."
    return rol, None


def _cliente_dicho(usuario=None):
    """(cliente_id, error); (None, None) si no se dijo."""
    dicho = texto("cliente")
    if not dicho:
        return None, None
    disponibles = {c.id for c in clientes_disponibles(usuario)}
    cliente, error = resolver(buscar_clientes(dicho), lambda c: c.nombre, "clientes",
                              f"No encontré el cliente {dicho}.")
    if cliente is None:
        return None, error
    if cliente.id not in disponibles:
        return None, f"{cliente.nombre} ya tiene una cuenta para su portal."
    return cliente.id, None


def _datos_usuario(usuario=None):
    """(datos para el servicio, error)."""
    datos = dichos("nombre", "documento", "telefono")
    rol, error = _rol_dicho()
    if error:
        return None, error
    if rol:
        datos["rol"] = rol
    cliente_id, error = _cliente_dicho(usuario)
    if error:
        return None, error
    if cliente_id:
        datos["cliente_id"] = cliente_id
    return datos, None


def _nombre_cliente(cliente_id):
    from app.models import Cliente

    cliente = db.session.get(Cliente, cliente_id) if cliente_id else None
    return cliente.nombre if cliente else None


@usuarios("consultar", "Consultar usuarios",
          "datos de una cuenta (rol, correo, estado, portal o vehiculo); sin usuario, cuantas hay por rol",
          modifica=False)
def consultar_usuarios():
    if not texto("usuario"):
        cuentas = db.session.query(Usuario).all()
        activas = [u for u in cuentas if u.activo]
        por_rol = [f"{sum(1 for u in activas if u.rol == rol)} {etiqueta_rol(rol).lower()}"
                   for rol in (Rol.ADMIN, Rol.DESPACHADOR, Rol.CONDUCTOR, Rol.CLIENTE)]
        return responder(f"Hay {cantidad(len(activas), 'cuenta activa', 'cuentas activas')}: "
                         f"{enumerar(por_rol)}; y {cantidad(len(cuentas) - len(activas), 'inactiva')}.")
    usuario, error = _usuario()
    if usuario is None:
        return responder(error)
    partes = [f"{usuario.nombre} es {etiqueta_rol(usuario.rol).lower()}, con el correo {usuario.correo}, "
              f"y su cuenta está {'activa' if usuario.activo else 'inactiva'}."]
    if usuario.cliente is not None:
        partes.append(f"Ve el portal de {usuario.cliente.nombre}.")
    if usuario.vehiculo is not None:
        partes.append(f"Tiene asignado el vehículo {usuario.vehiculo.placa}.")
    if abiertas := rutas_abiertas(usuario):
        partes.append(f"Tiene {cantidad(abiertas, 'ruta sin cerrar', 'rutas sin cerrar')}.")
    return responder(" ".join(partes))


@usuarios("crear", "Crear usuario",
          "crea una cuenta; requiere nombre, correo y rol (y cliente si el rol es cliente); la "
          "contrasena le llega al usuario por correo")
def crear_usuario_voz():
    if not credenciales_configuradas():
        return responder(SIN_CREDENCIALES.format("crear cuentas"))
    datos, error = _datos_usuario()
    if error:
        return responder(error)
    if texto("correo"):
        datos["correo"] = texto("correo")
    faltan = [etiqueta for campo, etiqueta in (("nombre", "el nombre"), ("correo", "el correo"),
                                                ("rol", "el rol"))
              if campo not in datos]
    if datos.get("rol") == Rol.CLIENTE and "cliente_id" not in datos:
        faltan.append("el cliente cuyo portal verá")
    if faltan:
        return responder(me_falta(faltan))
    datos["activo"] = True
    try:
        limpios = validar_usuario(datos, actor=g.usuario)
    except DatosInvalidos as error:
        return responder(frase_datos_invalidos(error))

    portal = (f", con acceso al portal de {_nombre_cliente(limpios['cliente_id'])}"
              if limpios["cliente_id"] else "")
    pendiente = exigir_confirmacion(
        f"Voy a crear la cuenta de {limpios['nombre']}, con el correo {limpios['correo']}, como "
        f"{etiqueta_rol(limpios['rol']).lower()}{portal}. {AVISO_CORREO}"
    )
    if pendiente:
        return pendiente
    try:
        usuario, generada, _ = crear_usuario(datos, actor=g.usuario)
    except DatosInvalidos as error:
        return _no_se_pudo(error)
    aviso_credencial_usuario(usuario, generada, ACCION_CREACION, g.usuario)
    db.session.commit()
    return responder(f"Listo, creé la cuenta de {usuario.nombre}. La contraseña va en camino a su correo.")


@usuarios("editar", "Editar usuario",
          "cambia nombre, documento, telefono, rol o el cliente del portal; el correo no; requiere usuario")
def editar_usuario_voz():
    usuario, error = _usuario()
    if usuario is None:
        return responder(error)
    if texto("correo"):
        return responder("El correo con el que se inicia sesión solo lo cambia un administrador en "
                         "pantalla, en Usuarios.")
    datos, error = _datos_usuario(usuario)
    if error:
        return responder(error)
    if not datos:
        return responder(f"¿Qué quieres cambiar de la cuenta de {usuario.nombre}? Puedo cambiar el nombre, "
                         "el documento, el teléfono, el rol o el cliente de su portal.")
    actuales = {"nombre": usuario.nombre, "documento": usuario.documento, "telefono": usuario.telefono,
                "rol": usuario.rol, "cliente_id": usuario.cliente.id if usuario.cliente else None}
    try:
        limpios = validar_usuario(datos, usuario, g.usuario)
    except DatosInvalidos as error:
        return responder(frase_datos_invalidos(error))
    cambios = diferencias(actuales, limpios, ("nombre", "documento", "telefono", "rol", "cliente_id"))
    if not cambios:
        return responder(f"La cuenta de {usuario.nombre} ya tiene esos datos; no hay nada que cambiar.")
    leidos = dict(cambios)
    if "rol" in leidos:
        antes, despues = leidos["rol"]
        leidos["rol"] = (etiqueta_rol(antes).lower(), etiqueta_rol(despues).lower())
    if "cliente_id" in leidos:
        antes, despues = leidos.pop("cliente_id")
        leidos["cliente"] = (_nombre_cliente(antes), _nombre_cliente(despues))
    consecuencias = consecuencias_edicion(usuario, limpios)

    pendiente = exigir_confirmacion(
        f"Voy a cambiar en la cuenta de {usuario.nombre} {leer_cambios(leidos, ETIQUETAS_USUARIO)}."
        + (f" Además, {enumerar(consecuencias)}." if consecuencias else "")
    )
    if pendiente:
        return pendiente
    try:
        editar_usuario(usuario, datos, actor=g.usuario)
    except DatosInvalidos as error:
        return _no_se_pudo(error)
    db.session.commit()
    return responder(f"Listo, actualicé la cuenta de {usuario.nombre}.")


def _estado_usuario(activo):
    usuario, error = _usuario()
    if usuario is None:
        return responder(error)
    try:
        verificar_cambio_estado(usuario, activo, g.usuario)
    except DatosInvalidos as error:
        return responder(frase_datos_invalidos(error))
    pendiente = exigir_confirmacion(
        f"Voy a {'activar' if activo else 'desactivar'} la cuenta de {usuario.nombre}, "
        f"{etiqueta_rol(usuario.rol).lower()}"
        + (". Podrá volver a iniciar sesión." if activo else ". No podrá iniciar sesión.")
    )
    if pendiente:
        return pendiente
    try:
        cambiar_estado_usuario(usuario, activo, actor=g.usuario)
    except DatosInvalidos as error:
        return _no_se_pudo(error)
    db.session.commit()
    return responder(f"Listo, la cuenta de {usuario.nombre} quedó {'activa' if activo else 'inactiva'}.")


@usuarios("activar", "Activar usuario", "reactiva una cuenta; requiere usuario")
def activar_usuario():
    return _estado_usuario(True)


@usuarios("desactivar", "Desactivar usuario",
          "desactiva una cuenta; no la propia, ni el ultimo administrador, ni un conductor con rutas "
          "sin cerrar; requiere usuario")
def desactivar_usuario():
    return _estado_usuario(False)


@usuarios("restablecer-contrasena", "Restablecer contraseña",
          "genera una contrasena nueva que le llega al usuario por correo; requiere usuario")
def restablecer_contrasena_voz():
    if not credenciales_configuradas():
        return responder(SIN_CREDENCIALES.format("restablecer contraseñas"))
    usuario, error = _usuario()
    if usuario is None:
        return responder(error)
    if usuario.id == g.usuario.id:
        return responder("Tu propia contraseña la cambias en pantalla, en Perfil.")
    pendiente = exigir_confirmacion(
        f"Voy a restablecer la contraseña de {usuario.nombre}; la anterior deja de servir. "
        f"La nueva le llega solo a su correo, {usuario.correo}; si no le llega, restablécela en pantalla."
    )
    if pendiente:
        return pendiente
    nueva = restablecer_contrasena(usuario)
    aviso_credencial_usuario(usuario, nueva, ACCION_RESTABLECIMIENTO, g.usuario)
    db.session.commit()
    return responder(f"Listo, restablecí la contraseña de {usuario.nombre}. La nueva va en camino a su correo.")


# --------------------------------------------------------------------------
# Configuracion del negocio
# --------------------------------------------------------------------------

configuracion = funcion_agrupada(
    ESPACIO, "configuracion", roles=ROLES, entidad="configuracion",
    descripcion="Configuracion del negocio: el centro de distribucion (origen de las rutas) y el "
                "catalogo de motivos de entrega fallida. 'Otro' no se renombra ni se desactiva.",
    parametros={
        "nombre_centro": {"type": "string", "description": "editar-centro: nombre del centro."},
        "latitud": {"type": "number", "description": "editar-centro: latitud del centro."},
        "longitud": {"type": "number", "description": "editar-centro: longitud del centro."},
        "motivo": {"type": "string",
                   "description": "agregar-motivo: el motivo nuevo. renombrar, activar y desactivar: el "
                                  "motivo existente."},
        "nuevo_nombre": {"type": "string", "description": "renombrar-motivo: el nombre nuevo."},
    },
)


def _motivo():
    buscado = texto("motivo")
    if not buscado:
        return None, me_falta(["el motivo"])
    return resolver(buscar_motivos(buscado), lambda m: m.nombre + ("" if m.activo else ", desactivado"),
                    "motivos", f"No encontré el motivo {buscado}.")


@configuracion("consultar", "Consultar configuración",
               "el centro de distribucion y los motivos de fallo activos y desactivados", modifica=False)
def consultar_configuracion():
    centro = centro_distribucion()
    motivos = catalogo(incluir_inactivos=True)
    activos = [m.nombre for m in motivos if m.activo]
    inactivos = [m.nombre for m in motivos if not m.activo]
    return responder(
        f"El centro de distribución es {centro['nombre']}, en latitud {valor_leido(centro['latitud'])} y "
        f"longitud {valor_leido(centro['longitud'])}. Motivos de fallo activos: {enumerar(activos)}."
        + (f" Desactivados: {enumerar(inactivos)}." if inactivos else "")
    )


@configuracion("editar-centro", "Editar centro de distribución",
               "cambia nombre o coordenadas del centro; afecta solo a las rutas nuevas o recalculadas")
def editar_centro_voz():
    actual = centro_distribucion()
    datos = dichos("nombre_centro", "latitud", "longitud")
    if not datos:
        return responder("¿Qué quieres cambiar del centro de distribución? Su nombre o sus coordenadas.")
    try:
        nombre, lat, lng = validar_centro(datos.get("nombre_centro", actual["nombre"]),
                                          datos.get("latitud", actual["latitud"]),
                                          datos.get("longitud", actual["longitud"]))
    except DatosInvalidos as error:
        return responder(frase_datos_invalidos(error))
    cambios = diferencias(actual, {"nombre": nombre, "latitud": lat, "longitud": lng},
                          ("nombre", "latitud", "longitud"))
    if not cambios:
        return responder("El centro ya tiene esos datos; no hay nada que cambiar.")
    pendiente = exigir_confirmacion(
        f"Voy a cambiar en el centro de distribución "
        f"{leer_cambios(cambios, {'nombre': 'el nombre', 'latitud': 'la latitud', 'longitud': 'la longitud'})}. "
        "Solo afecta a las rutas que se creen o se recalculen después."
    )
    if pendiente:
        return pendiente
    try:
        editar_centro(nombre, lat, lng, g.usuario.id)
    except DatosInvalidos as error:
        return _no_se_pudo(error)
    db.session.commit()
    return responder("Listo, actualicé el centro de distribución.")


@configuracion("agregar-motivo", "Agregar motivo de fallo", "agrega un motivo al catalogo; requiere motivo")
def agregar_motivo_voz():
    if not texto("motivo"):
        return responder(me_falta(["el motivo nuevo"]))
    try:
        nombre = validar_agregar_motivo(texto("motivo"))
    except DatosInvalidos as error:
        return responder(frase_datos_invalidos(error))
    pendiente = exigir_confirmacion(
        f"Voy a agregar el motivo de fallo «{nombre}»; los conductores lo verán desde ya."
    )
    if pendiente:
        return pendiente
    try:
        agregar_motivo(nombre)
    except DatosInvalidos as error:
        return _no_se_pudo(error)
    db.session.commit()
    return responder(f"Listo, agregué el motivo «{nombre}».")


@configuracion("renombrar-motivo", "Renombrar motivo de fallo",
               "cambia el nombre de un motivo; las entregas ya registradas conservan su texto; "
               "requiere motivo y nuevo_nombre")
def renombrar_motivo_voz():
    motivo, error = _motivo()
    if motivo is None:
        return responder(error)
    if not texto("nuevo_nombre"):
        return responder(me_falta(["el nombre nuevo"]))
    try:
        nombre = validar_renombrar_motivo(motivo, texto("nuevo_nombre"))
    except DatosInvalidos as error:
        return responder(frase_datos_invalidos(error))
    pendiente = exigir_confirmacion(
        f"Voy a renombrar el motivo «{motivo.nombre}» a «{nombre}». Las entregas fallidas ya registradas "
        "conservan el texto anterior."
    )
    if pendiente:
        return pendiente
    try:
        renombrar_motivo(motivo, nombre)
    except DatosInvalidos as error:
        return _no_se_pudo(error)
    db.session.commit()
    return responder(f"Listo, el motivo ahora se llama «{nombre}».")


def _estado_motivo(activo):
    motivo, error = _motivo()
    if motivo is None:
        return responder(error)
    try:
        validar_cambio_estado_motivo(motivo, activo)
    except DatosInvalidos as error:
        return responder(frase_datos_invalidos(error))
    pendiente = exigir_confirmacion(
        f"Voy a {'activar' if activo else 'desactivar'} el motivo «{motivo.nombre}»"
        + (": volverá a ofrecerse a los conductores." if activo
           else ": deja de ofrecerse a los conductores; la analítica conserva lo registrado.")
    )
    if pendiente:
        return pendiente
    try:
        cambiar_estado_motivo(motivo, activo)
    except DatosInvalidos as error:
        return _no_se_pudo(error)
    db.session.commit()
    return responder(f"Listo, el motivo «{motivo.nombre}» quedó {'activo' if activo else 'desactivado'}.")


@configuracion("activar-motivo", "Activar motivo de fallo", "reactiva un motivo; requiere motivo")
def activar_motivo():
    return _estado_motivo(True)


@configuracion("desactivar-motivo", "Desactivar motivo de fallo", "desactiva un motivo; requiere motivo")
def desactivar_motivo():
    return _estado_motivo(False)


# --------------------------------------------------------------------------
# Bitacora (auditoria)
# --------------------------------------------------------------------------

LIMITE_BITACORA = 5
MAXIMO_BITACORA = 10


@funcion_asistente(
    ESPACIO, "bitacora", roles=ROLES,
    descripcion="Ultimos registros de la auditoria (quien hizo que, desde pantalla o voz), del mas "
                "reciente al mas antiguo, con filtros opcionales por fecha, usuario y entidad.",
    parametros={
        "desde": {"type": "string", "description": "Fecha inicial AAAA-MM-DD (opcional)."},
        "hasta": {"type": "string", "description": "Fecha final AAAA-MM-DD (opcional)."},
        "usuario": {"type": "string", "description": "Nombre o correo de quien hizo la accion (opcional)."},
        "entidad": {"type": "string",
                    "description": "Sobre que: pedido, producto, cliente, sede, vehiculo, ruta, usuario, "
                                   "configuracion o solicitud (opcional)."},
        "cantidad": {"type": "integer", "description": "Cuantos registros leer: 5 por defecto, hasta 10."},
    },
)
def bitacora():
    consulta = db.session.query(Auditoria)
    filtros = []
    desde = fecha_iso(g.argumentos.get("desde"), None)
    hasta = fecha_iso(g.argumentos.get("hasta"), None)
    if (g.argumentos.get("desde") and desde is None) or (g.argumentos.get("hasta") and hasta is None):
        return responder("No entendí la fecha; dímela como año, mes y día.")
    if desde:
        consulta = consulta.filter(Auditoria.registrado_en >= datetime.combine(desde, time.min))
        filtros.append(f"desde el {desde.strftime('%d/%m')}")
    if hasta:
        consulta = consulta.filter(Auditoria.registrado_en < datetime.combine(hasta + timedelta(days=1), time.min))
        filtros.append(f"hasta el {hasta.strftime('%d/%m')}")
    if texto("usuario"):
        usuario, error = _usuario()
        if usuario is None:
            return responder(error)
        consulta = consulta.filter(Auditoria.usuario_id == usuario.id)
        filtros.append(f"de {usuario.nombre}")
    if texto("entidad"):
        dicha = normalizar_texto(texto("entidad")).replace(" ", "_")
        existentes = [fila[0] for fila in db.session.query(Auditoria.entidad).distinct()]
        elegidas = [e for e in existentes if dicha in (e, e + "s", e + "es") or e.startswith(dicha.rstrip("s"))]
        if not elegidas:
            return responder(f"No hay registros sobre {texto('entidad')}.")
        consulta = consulta.filter(Auditoria.entidad.in_(elegidas))
        filtros.append(f"sobre {texto('entidad')}")

    total = consulta.count()
    if not total:
        return responder("No hay registros en la bitácora" + (f" {' '.join(filtros)}." if filtros else "."))
    pedidos = entero(g.argumentos.get("cantidad"))
    limite = min(max(pedidos or LIMITE_BITACORA, 1), MAXIMO_BITACORA)
    registros = consulta.order_by(Auditoria.registrado_en.desc(), Auditoria.id.desc()).limit(limite).all()

    def leer(r):
        dia = "hoy" if r.registrado_en.date() == hoy() else r.registrado_en.strftime("el %d/%m")
        quien = r.usuario_nombre + (f", {etiqueta_rol(r.rol).lower()}" if r.rol else "")
        fallo = "" if r.resultado == "exito" else ", sin éxito"
        return f"{dia} a las {r.registrado_en.strftime('%H:%M')}, {quien}, por {r.origen}{fallo}: {r.resumen}"

    return responder(
        f"Hay {cantidad(total, 'registro')}" + (f" {' '.join(filtros)}" if filtros else "")
        + (f"; los {len(registros)} más recientes: " if total > len(registros) else ": ")
        + " ".join(f"{leer(r).rstrip('.')}." for r in registros)
    )
