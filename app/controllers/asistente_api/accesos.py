"""Accesos y seguridad por voz (solo el admin): roles, permisos, contrasenas y errores.

Paridad con Administracion > Roles, Usuarios > Permisos, Usuarios >
Contrasenas y Administracion > Errores. Las reglas son las de la pantalla
(`app.services.permisos` y `app.services.usuarios`): por voz no se reparte
nada que en pantalla no se pueda, y cada operacion exige el mismo permiso
(app/permisos.py, PERMISOS_VOZ).

Lo que no se hace por voz (ver app/asistentes/cobertura.py): escribir una
contrasena (ni la propia ni la de otra cuenta), cambiar el propio correo de
inicio de sesion, "Olvide mi contrasena" y provocar un error de prueba. Los
intentos de acceso solo se consultan: el bloqueo vence solo, en un minuto.
"""

import re

from flask import g

from app.controllers.asistente_api import cantidad, exigir_confirmacion, funcion_agrupada, funcion_asistente, responder
from app.controllers.asistente_api.administracion import ROLES_DICHOS, _no_se_pudo, _usuario
from app.controllers.asistente_api.comun import (
    dichos, entero, enumerar, fecha_larga, frase_datos_invalidos, me_falta, resolver, texto,
)
from app.extensions import db
from app.models import ErrorSistema, Rol, Usuario, normalizar_texto
from app.permisos import ASIGNABLES, PERMISOS, nombre as nombre_permiso
from app.services import acceso
from app.services import permisos as servicio
from app.services.busqueda_voz import Coincidencias
from app.services.notificaciones import etiqueta_rol
from app.services.usuarios import (
    cambiar_exigencia, cuentas_contrasena_administrada, exigir_cambio_cuentas, verificar_exigencia,
)
from app.services.validacion import DatosInvalidos
from app.tiempo import ahora, hoy

ESPACIO = "admin"
ROLES = (Rol.ADMIN,)


def _lista(valor):
    """Lista dicha: ["ver pedidos", "ver rutas"] o "ver pedidos, ver rutas y ver inventario"."""
    if isinstance(valor, (list, tuple)):
        partes = [str(v) for v in valor]
    else:
        partes = re.split(r",|;|\by\b", str(valor or ""))
    return [p.strip() for p in partes if p and p.strip()]


def _nombres(permisos):
    orden = list(PERMISOS)
    return [nombre_permiso(p) for p in sorted(permisos, key=orden.index)]


# --------------------------------------------------------------------------
# Busquedas por lo que se dijo
# --------------------------------------------------------------------------

def buscar_permisos(dicho, entre=PERMISOS):
    """Permiso por su clave ("pedidos.ver") o su nombre ("ver pedidos", "gestionar inventario")."""
    buscado = normalizar_texto(dicho)
    if not buscado:
        return Coincidencias([])
    claves = list(entre)
    identicos = [p for p in claves
                 if buscado in (normalizar_texto(p), normalizar_texto(nombre_permiso(p)),
                                normalizar_texto(nombre_permiso(p).split(" (")[0]))]
    if identicos:
        return Coincidencias(identicos)
    palabras = buscado.split()
    return Coincidencias([p for p in claves
                          if all(pal in normalizar_texto(f"{p} {nombre_permiso(p)}").split() for pal in palabras)])


def buscar_roles(dicho):
    """Rol por su nombre; los del sistema tambien como se dicen ("gestor", "admin")."""
    buscado = normalizar_texto(dicho)
    if not buscado:
        return Coincidencias([])
    todos = servicio.roles()
    clave = ROLES_DICHOS.get(buscado)
    identicos = [r for r in todos if normalizar_texto(r.nombre) == buscado
                 or (clave is not None and r.clave_sistema == clave)]
    if identicos:
        return Coincidencias(identicos)
    return Coincidencias([r for r in todos
                          if all(p in normalizar_texto(r.nombre).split() for p in buscado.split())])


def _rol(campo="rol"):
    dicho = texto(campo)
    if not dicho:
        return None, me_falta(["el rol"])
    return resolver(buscar_roles(dicho), lambda r: r.nombre, "roles", f"No encontré el rol {dicho}.")


def _resolver_permiso_dicho(dicho):
    """(permiso asignable, None) o (None, mensaje). Avisa si se nombro uno que no se reparte."""
    coincidencias = buscar_permisos(dicho, ASIGNABLES)
    if coincidencias.vacio and not buscar_permisos(dicho).vacio:
        return None, (f"«{nombre_permiso(buscar_permisos(dicho).elementos[0])}» no se reparte: solo lo "
                      "tienen los administradores o depende del tipo de cuenta.")
    return resolver(coincidencias, nombre_permiso, "permisos", f"No encontré el permiso {dicho}.")


def _permiso():
    dicho = texto("permiso")
    if not dicho:
        return None, me_falta(["el permiso"])
    return _resolver_permiso_dicho(dicho)


# --------------------------------------------------------------------------
# Roles
# --------------------------------------------------------------------------

roles = funcion_agrupada(
    ESPACIO, "roles", roles=ROLES, entidad="rol",
    descripcion="Roles y su reparto de permisos (Administracion > Roles). El de administrador y el de "
                "cliente no se editan; los roles del sistema no se renombran ni se eliminan. Gestionar "
                "usuarios, contrasenas y roles no se reparte.",
    parametros={
        "rol": {"type": "string",
                "description": "consultar, editar, agregar-permiso, quitar-permiso y eliminar: el rol "
                               "existente. En consultar, sin rol da la lista."},
        "nombre": {"type": "string", "description": "crear: nombre del rol nuevo. editar: el nombre nuevo."},
        "descripcion": {"type": "string", "description": "crear y editar: descripcion (opcional)."},
        "permisos": {"type": "array", "items": {"type": "string"},
                     "description": "crear: los permisos del rol, por su nombre (p. ej. 'ver pedidos')."},
        "permiso": {"type": "string",
                    "description": "agregar-permiso y quitar-permiso: el permiso, por su nombre."},
    },
)


@roles("consultar", "Consultar roles",
       "sin rol, los roles con cuantos permisos y cuentas tiene cada uno; con rol, sus permisos y "
       "quienes lo tienen", modifica=False)
def consultar_roles():
    if not texto("rol"):
        partes = [f"{r.nombre}, con {cantidad(len(servicio.permisos_de(r)), 'permiso')} y "
                  f"{cantidad(len(servicio.usuarios_con_rol(r)), 'cuenta')}" for r in servicio.roles()]
        return responder(f"Hay {cantidad(len(partes), 'rol', 'roles')}: {'; '.join(partes)}.")
    rol, error = _rol()
    if rol is None:
        return responder(error)
    usuarios = servicio.usuarios_con_rol(rol)
    return responder(
        f"El rol {rol.nombre} da: {enumerar(_nombres(servicio.permisos_de(rol)))}. "
        + (f"Lo tienen {enumerar([u.nombre for u in usuarios])}." if usuarios else "Ninguna cuenta lo tiene.")
        + ("" if servicio.es_editable(rol) else " Es un rol del sistema que no se edita.")
    )


@roles("crear", "Crear rol", "crea un rol con sus permisos; requiere nombre y permisos")
def crear_rol_voz():
    faltan = ([] if texto("nombre") else ["el nombre del rol"]) + ([] if _lista(g.argumentos.get("permisos"))
                                                                   else ["los permisos"])
    if faltan:
        return responder(me_falta(faltan))
    elegidos = []
    for dicho in _lista(g.argumentos.get("permisos")):
        permiso, error = _resolver_permiso_dicho(dicho)
        if permiso is None:
            return responder(error)
        elegidos.append(permiso)
    datos = {"nombre": texto("nombre"), "descripcion": texto("descripcion"), "permisos": elegidos}
    try:
        nombre_rol, _, permisos = servicio.validar_rol(datos)
    except DatosInvalidos as error:
        return responder(frase_datos_invalidos(error))
    pendiente = exigir_confirmacion(
        f"Voy a crear el rol {nombre_rol} con {cantidad(len(permisos), 'permiso')}: "
        f"{enumerar(_nombres(permisos))}. Después lo asignas a las cuentas."
    )
    if pendiente:
        return pendiente
    try:
        rol = servicio.crear_rol(datos)
    except DatosInvalidos as error:
        return _no_se_pudo(error)
    db.session.commit()
    return responder(f"Listo, creé el rol {rol.nombre}.")


def _editar_rol(rol, datos, resumen):
    """Valida, pide confirmacion y aplica `datos` (nombre, descripcion, permisos) al rol."""
    try:
        servicio.validar_rol(datos, rol)
    except DatosInvalidos as error:
        return responder(frase_datos_invalidos(error))
    pendiente = exigir_confirmacion(resumen)
    if pendiente:
        return pendiente
    try:
        servicio.editar_rol(rol, datos)
    except DatosInvalidos as error:
        return _no_se_pudo(error)
    db.session.commit()
    return responder(f"Listo, actualicé el rol {rol.nombre}. Aplica desde la siguiente acción de cada cuenta.")


def _datos_rol(rol, **cambios):
    datos = {"nombre": rol.nombre, "descripcion": rol.descripcion or "",
             "permisos": sorted(servicio.permisos_de(rol) & set(ASIGNABLES))}
    datos.update(cambios)
    return datos


@roles("editar", "Editar rol", "cambia el nombre o la descripcion de un rol; requiere rol")
def editar_rol_voz():
    rol, error = _rol()
    if rol is None:
        return responder(error)
    cambios = {"descripcion" if campo == "descripcion" else "nombre": valor
               for campo, valor in dichos("nombre", "descripcion").items()}
    if not cambios:
        return responder(f"¿Qué quieres cambiar del rol {rol.nombre}? Su nombre o su descripción.")
    if "nombre" in cambios and rol.es_sistema:
        return responder(f"El rol {rol.nombre} es del sistema y no se renombra.")
    partes = ([f"el nombre a {cambios['nombre']}"] if "nombre" in cambios else []) + (
        ["la descripción"] if "descripcion" in cambios else [])
    return _editar_rol(rol, _datos_rol(rol, **cambios),
                       f"Voy a cambiar en el rol {rol.nombre} {enumerar(partes)}.")


def _cambiar_permiso_rol(agregar):
    rol, error = _rol()
    if rol is None:
        return responder(error)
    if not servicio.es_editable(rol):
        try:   # el mismo mensaje de la pantalla: por que este rol no se edita
            servicio.validar_rol(_datos_rol(rol), rol)
        except DatosInvalidos as error:
            return responder(frase_datos_invalidos(error))
    permiso, error = _permiso()
    if permiso is None:
        return responder(error)
    actuales = set(servicio.permisos_de(rol) & set(ASIGNABLES))
    if (permiso in actuales) == agregar:
        return responder(f"El rol {rol.nombre} {'ya da' if agregar else 'no da'} «{nombre_permiso(permiso)}».")
    nuevos = actuales | {permiso} if agregar else actuales - {permiso}
    afectadas = len(servicio.usuarios_con_rol(rol))
    return _editar_rol(
        rol, _datos_rol(rol, permisos=sorted(nuevos)),
        f"Voy a {'agregar' if agregar else 'quitar'} «{nombre_permiso(permiso)}» "
        f"{'al' if agregar else 'del'} rol {rol.nombre}; afecta a {cantidad(afectadas, 'cuenta')}.",
    )


@roles("agregar-permiso", "Agregar permiso a rol", "suma un permiso al rol; requiere rol y permiso")
def agregar_permiso_rol():
    return _cambiar_permiso_rol(True)


@roles("quitar-permiso", "Quitar permiso de rol", "quita un permiso del rol; requiere rol y permiso")
def quitar_permiso_rol():
    return _cambiar_permiso_rol(False)


@roles("eliminar", "Eliminar rol", "borra un rol creado por el administrador; requiere rol")
def eliminar_rol_voz():
    rol, error = _rol()
    if rol is None:
        return responder(error)
    if rol.es_sistema:
        return responder(f"El rol {rol.nombre} es del sistema y no se elimina.")
    afectadas = [u.nombre for u in rol.usuarios]
    pendiente = exigir_confirmacion(
        f"Voy a eliminar el rol {rol.nombre}"
        + (f"; {enumerar(afectadas)} dejan de tenerlo." if afectadas else "; ninguna cuenta lo tiene.")
    )
    if pendiente:
        return pendiente
    nombre_rol = rol.nombre
    try:
        servicio.eliminar_rol(rol)
    except DatosInvalidos as error:
        return _no_se_pudo(error)
    db.session.commit()
    return responder(f"Listo, eliminé el rol {nombre_rol}.")


# --------------------------------------------------------------------------
# Permisos de una cuenta
# --------------------------------------------------------------------------

permisos = funcion_agrupada(
    ESPACIO, "permisos", roles=ROLES, entidad="usuario",
    descripcion="Roles adicionales y ajustes de permisos de una cuenta (Usuarios > Permisos). Los "
                "administradores ya tienen todo y las cuentas de cliente solo su portal: no llevan "
                "ajustes.",
    parametros={
        "usuario": {"type": "string", "description": "Nombre, correo, documento o telefono de la cuenta. Requerido."},
        "rol": {"type": "string", "description": "asignar-rol y quitar-rol: el rol adicional."},
        "permiso": {"type": "string",
                    "description": "conceder, denegar y quitar-ajuste: el permiso, por su nombre."},
    },
)


def _cuenta_con_permisos():
    usuario, error = _usuario()
    if usuario is None:
        return None, error
    rechazo = servicio.rechazo_permisos(usuario)
    return (None, f"{usuario.nombre}: {rechazo}") if rechazo else (usuario, None)


def _guardar_permisos(usuario, rol_ids, ajustes, resumen):
    try:
        cambios = servicio.guardar_permisos_usuario(usuario, rol_ids, ajustes)
    except DatosInvalidos as error:
        db.session.rollback()
        return responder(frase_datos_invalidos(error))
    db.session.rollback()   # solo se valido: se aplica tras confirmar
    if not cambios:
        return responder(f"La cuenta de {usuario.nombre} ya está así; no hay nada que cambiar.")
    pendiente = exigir_confirmacion(resumen)
    if pendiente:
        return pendiente
    try:
        servicio.guardar_permisos_usuario(usuario, rol_ids, ajustes)
    except DatosInvalidos as error:
        return _no_se_pudo(error)
    db.session.commit()
    return responder(f"Listo, actualicé los permisos de {usuario.nombre}. Aplican desde su siguiente acción.")


def _estado_actual(usuario):
    rol_ids = [r.id for r in usuario.roles_adicionales]
    ajustes = {a.permiso: (servicio.AJUSTE_CONCEDER if a.concedido else servicio.AJUSTE_DENEGAR)
               for a in usuario.ajustes_permisos}
    return rol_ids, ajustes


@permisos("consultar", "Consultar permisos de usuario",
          "los permisos que tiene una cuenta, de que roles le vienen y sus ajustes; requiere usuario",
          modifica=False)
def consultar_permisos():
    usuario, error = _usuario()
    if usuario is None:
        return responder(error)
    efectivos = servicio.permisos_efectivos(usuario)
    partes = [f"{usuario.nombre} es {etiqueta_rol(usuario.rol).lower()}"
              + (f", con los roles adicionales {enumerar([r.nombre for r in usuario.roles_adicionales])}"
                 if usuario.roles_adicionales else "")
              + f". Puede: {enumerar(_nombres(efectivos))}."]
    concedidos = [a.permiso for a in usuario.ajustes_permisos if a.concedido]
    denegados = [a.permiso for a in usuario.ajustes_permisos if not a.concedido]
    if concedidos:
        partes.append(f"Se le concedió aparte: {enumerar(_nombres(concedidos))}.")
    if denegados:
        partes.append(f"Se le negó: {enumerar(_nombres(denegados))}.")
    return responder(" ".join(partes))


def _cambiar_rol_adicional(asignar):
    usuario, error = _cuenta_con_permisos()
    if usuario is None:
        return responder(error)
    rol, error = _rol()
    if rol is None:
        return responder(error)
    rol_ids, ajustes = _estado_actual(usuario)
    if (rol.id in rol_ids) == asignar:
        return responder(f"{usuario.nombre} {'ya tiene' if asignar else 'no tiene'} el rol {rol.nombre}.")
    nuevos = rol_ids + [rol.id] if asignar else [i for i in rol_ids if i != rol.id]
    return _guardar_permisos(
        usuario, nuevos, ajustes,
        f"Voy a {'asignarle a' if asignar else 'quitarle a'} {usuario.nombre} el rol {rol.nombre}"
        + (f", que da {enumerar(_nombres(servicio.permisos_de(rol)))}." if asignar else "."),
    )


@permisos("asignar-rol", "Asignar rol a usuario", "suma un rol adicional a la cuenta; requiere usuario y rol")
def asignar_rol():
    return _cambiar_rol_adicional(True)


@permisos("quitar-rol", "Quitar rol a usuario", "quita un rol adicional de la cuenta; requiere usuario y rol")
def quitar_rol():
    return _cambiar_rol_adicional(False)


def _ajustar(valor, frase):
    usuario, error = _cuenta_con_permisos()
    if usuario is None:
        return responder(error)
    permiso, error = _permiso()
    if permiso is None:
        return responder(error)
    rol_ids, ajustes = _estado_actual(usuario)
    ajustes[permiso] = valor
    return _guardar_permisos(usuario, rol_ids, ajustes,
                             f"Voy a {frase} «{nombre_permiso(permiso)}» a {usuario.nombre}.")


@permisos("conceder", "Conceder permiso a usuario",
          "da un permiso a la cuenta aunque sus roles no lo den; requiere usuario y permiso")
def conceder_permiso():
    return _ajustar(servicio.AJUSTE_CONCEDER, "concederle")


@permisos("denegar", "Denegar permiso a usuario",
          "quita un permiso a la cuenta aunque sus roles lo den; requiere usuario y permiso")
def denegar_permiso():
    return _ajustar(servicio.AJUSTE_DENEGAR, "negarle")


@permisos("quitar-ajuste", "Quitar ajuste de permiso",
          "deja el permiso segun los roles de la cuenta, sin conceder ni denegar; requiere usuario y permiso")
def quitar_ajuste():
    return _ajustar("", "dejar según sus roles")


# --------------------------------------------------------------------------
# Contrasenas: exigencia de cambio e intentos de acceso
# --------------------------------------------------------------------------

contrasenas = funcion_agrupada(
    ESPACIO, "contrasenas", roles=ROLES, entidad="usuario",
    descripcion="Estado de las contrasenas (Usuarios > Contrasenas), sin decir nunca una contrasena: "
                "cuando cambio cada una, si debe cambiarla, los intentos fallidos de inicio de sesion y "
                "exigir o quitar el cambio. Solo de gestores y conductores. Para una contrasena nueva, "
                "admin/usuarios restablecer-contrasena.",
    parametros={
        "usuario": {"type": "string",
                    "description": "consultar, exigir-cambio y quitar-exigencia: nombre, correo, documento o telefono de la cuenta. "
                                   "En consultar, sin usuario da el resumen."},
        "rol": {"type": "string", "enum": ["conductor", "gestor"],
                "description": "exigir-cambio-masivo: solo las cuentas de ese rol (opcional; sin el, "
                               "gestores y conductores)."},
    },
)


def _intentos_de(usuario):
    return acceso.resumen([usuario.correo]).get(usuario.correo, (0, 0))


@contrasenas("consultar", "Consultar contraseñas",
             "sin usuario, cuantas cuentas deben cambiarla; con usuario, cuando la cambio, si debe "
             "cambiarla y sus intentos fallidos", modifica=False)
def consultar_contrasenas():
    if not texto("usuario"):
        cuentas = db.session.query(Usuario).filter(Usuario.activo.is_(True)).all()
        pendientes = [u.nombre for u in cuentas if u.debe_cambiar_contrasena]
        return responder(
            f"De {cantidad(len(cuentas), 'cuenta activa', 'cuentas activas')}, "
            + (f"{cantidad(len(pendientes), 'debe', 'deben')} cambiar su contraseña: {enumerar(pendientes)}."
               if pendientes else "ninguna tiene pendiente cambiar su contraseña.")
        )
    usuario, error = _usuario()
    if usuario is None:
        return responder(error)
    cambio = usuario.contrasena_actualizada_en
    fallidos, espera = _intentos_de(usuario)
    return responder(
        f"La contraseña de {usuario.nombre} "
        + (f"cambió el {fecha_larga(cambio.date())}" if cambio else "no tiene fecha de cambio registrada")
        + (" y debe cambiarla al entrar." if usuario.debe_cambiar_contrasena else " y no tiene cambio pendiente.")
        + (f" Su inicio de sesión está bloqueado {cantidad(espera, 'segundo')} más." if espera
           else f" Lleva {cantidad(fallidos, 'intento fallido', 'intentos fallidos')} seguidos." if fallidos
           else "")
    )


@contrasenas("intentos", "Consultar intentos de acceso",
             "las cuentas con intentos fallidos de inicio de sesion y las bloqueadas ahora", modifica=False)
def intentos_acceso():
    cuentas = db.session.query(Usuario).order_by(Usuario.nombre).all()
    estado = acceso.resumen(u.correo for u in cuentas)
    con_fallos = [(u, *estado[u.correo]) for u in cuentas if u.correo in estado]
    if not con_fallos:
        return responder("Ninguna cuenta tiene intentos fallidos recientes de inicio de sesión.")
    maximo = acceso.maximo_intentos()
    partes = [f"{u.nombre}, bloqueada {cantidad(espera, 'segundo')} más" if espera
              else f"{u.nombre}, {fallidos} de {maximo}" for u, fallidos, espera in con_fallos]
    return responder(f"Con intentos fallidos: {'; '.join(partes)}. Al llegar a {maximo} el inicio de "
                     "sesión se bloquea un minuto y se desbloquea solo.")


def _exigencia(exigir):
    usuario, error = _usuario()
    if usuario is None:
        return responder(error)
    try:
        verificar_exigencia(usuario, exigir, g.usuario)
    except DatosInvalidos as error:
        return responder(frase_datos_invalidos(error))
    pendiente = exigir_confirmacion(
        f"Voy a exigirle a {usuario.nombre} que cambie su contraseña: entra con la que ya conoce y el "
        "sistema le pide una nueva, también si tiene una sesión abierta." if exigir
        else f"Voy a quitarle a {usuario.nombre} la obligación de cambiar su contraseña."
    )
    if pendiente:
        return pendiente
    try:
        cambiar_exigencia(usuario, exigir, g.usuario)
    except DatosInvalidos as error:
        return _no_se_pudo(error)
    db.session.commit()
    return responder(f"Listo, {usuario.nombre} "
                     + ("deberá cambiar su contraseña." if exigir else "ya no está obligado a cambiarla."))


@contrasenas("exigir-cambio", "Exigir cambio de contraseña",
             "obliga a la cuenta a cambiar su contrasena al entrar; requiere usuario")
def exigir_cambio():
    return _exigencia(True)


@contrasenas("quitar-exigencia", "Quitar exigencia de cambio de contraseña",
             "quita la obligacion de cambiar la contrasena; requiere usuario")
def quitar_exigencia():
    return _exigencia(False)


@contrasenas("exigir-cambio-masivo", "Exigir cambio de contraseña",
             "obliga a todas las cuentas de gestores y conductores (o solo las del rol dicho) a cambiar "
             "su contrasena, p. ej. ante una fuga")
def exigir_cambio_todos():
    dicho = normalizar_texto(texto("rol"))
    rol = ROLES_DICHOS.get(dicho) if dicho else None
    if dicho and rol not in Rol.CONTRASENA_ADMINISTRADA:
        return responder("Solo se exige a gestores logísticos o conductores; cada administrador y cada "
                         "cliente cambian la suya.")
    ids = cuentas_contrasena_administrada(rol=rol)
    por_marcar = db.session.query(Usuario).filter(Usuario.id.in_(ids),
                                                  Usuario.debe_cambiar_contrasena.is_(False)).count()
    quienes = f"de {etiqueta_rol(rol).lower()}" if rol else "de gestores logísticos y conductores"
    if not por_marcar:
        return responder(f"Todas las cuentas {quienes} ya deben cambiar su contraseña.")
    pendiente = exigir_confirmacion(
        f"Voy a exigir el cambio de contraseña a {cantidad(por_marcar, 'cuenta')} {quienes}; las sesiones "
        "abiertas quedarán retenidas en el perfil hasta que la cambien."
    )
    if pendiente:
        return pendiente
    marcadas = exigir_cambio_cuentas(ids)
    db.session.commit()
    return responder(f"Listo, {cantidad(marcadas, 'cuenta')} deberán cambiar su contraseña.")


# --------------------------------------------------------------------------
# Errores del sistema
# --------------------------------------------------------------------------

LIMITE_ERRORES = 3
MAXIMO_ERRORES = 10


@funcion_asistente(
    ESPACIO, "errores", roles=ROLES,
    descripcion="Registro de errores del sistema (Administracion > Errores): los mas recientes, con su "
                "numero de referencia, o el detalle de uno por su numero.",
    parametros={
        "numero": {"type": "integer", "description": "Numero de referencia de un error (opcional)."},
        "cantidad": {"type": "integer", "description": "Cuantos leer: 3 por defecto, hasta 10."},
    },
)
def errores():
    def leer(e):
        dia = "hoy" if e.registrado_en.date() == hoy() else e.registrado_en.strftime("el %d/%m")
        quien = f", de {e.usuario_nombre}" if e.usuario_nombre else ""
        return (f"número {e.id}, {dia} a las {e.registrado_en.strftime('%H:%M')}{quien}, en {e.metodo} "
                f"{e.ruta}: {e.tipo}")

    numero = entero(g.argumentos.get("numero"))
    if numero is not None:
        registro = db.session.get(ErrorSistema, numero)
        if registro is None:
            return responder(f"No hay un error con el número {numero}.")
        return responder(f"Error {leer(registro)}. Mensaje: {registro.mensaje[:300]}.")
    total = db.session.query(ErrorSistema).count()
    if not total:
        return responder("No hay errores registrados.")
    limite = min(max(entero(g.argumentos.get("cantidad")) or LIMITE_ERRORES, 1), MAXIMO_ERRORES)
    recientes = db.session.query(ErrorSistema).order_by(ErrorSistema.id.desc()).limit(limite).all()
    del_dia = db.session.query(ErrorSistema).filter(
        ErrorSistema.registrado_en >= ahora().replace(hour=0, minute=0, second=0, microsecond=0)).count()
    return responder(
        f"Hay {cantidad(total, 'error registrado', 'errores registrados')}, {del_dia} de hoy. "
        f"{'El más reciente' if len(recientes) == 1 else f'Los {len(recientes)} más recientes'}: "
        + "; ".join(leer(e) for e in recientes) + "."
    )
