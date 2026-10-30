"""Roles y permisos (RF1 / RNF5): reglas compartidas por la pantalla y el login.

Permisos de una cuenta, en este orden:
1. Los de su rol principal (`usuarios.rol`), con el reparto que tenga ese rol
   del sistema en la base. Un administrador los tiene todos, siempre, salvo la
   ruta del conductor y el portal del cliente.
2. Mas los de sus roles adicionales.
3. Con sus ajustes propios: un permiso concedido se suma y uno denegado se
   quita, aunque sus roles lo den.

Solo se reparten los permisos ASIGNABLES (app/permisos.py): ni un rol ni un
ajuste da gestionar usuarios, contrasenas o roles, ni la ruta del conductor
o el portal del cliente. Una cuenta de cliente no lleva roles adicionales ni
ajustes: solo ve su portal.

Ninguna funcion hace commit.
"""

from flask import g, has_app_context

from app.extensions import db
from app.models import PermisoUsuario, Rol, RolAcceso, RolPermiso, normalizar_texto
from app.permisos import (
    ASIGNABLES, PERMISOS, PERMISOS_POR_DEFECTO, PROPIOS, ROLES_EDITABLES, SOLO_ADMIN, TODOS_ADMIN,
    nombre,
)
from app.services.validacion import DatosInvalidos, Validador

LARGO_NOMBRE = 60
LARGO_DESCRIPCION = 200

DESCRIPCIONES_SISTEMA = {
    Rol.ADMIN: "Administra el centro de distribución: tiene todos los permisos.",
    Rol.DESPACHADOR: "Opera pedidos, rutas, inventario, clientes y vehículos.",
    Rol.CONDUCTOR: "Recorre su ruta del día y registra las entregas.",
    Rol.CLIENTE: "Consulta los pedidos de su empresa en el portal.",
}

AJUSTE_CONCEDER = "conceder"
AJUSTE_DENEGAR = "denegar"


# --------------------------------------------------------------------------
# Permisos efectivos
# --------------------------------------------------------------------------

def permisos_rol_sistema(clave):
    """El reparto actual de un rol del sistema (el de una base nueva si aun no esta)."""
    if clave == Rol.ADMIN:
        return TODOS_ADMIN
    rol = db.session.query(RolAcceso).filter_by(clave_sistema=clave).first()
    if rol is None:
        return PERMISOS_POR_DEFECTO.get(clave, frozenset())
    return frozenset(rol.permisos & set(ASIGNABLES)) | PROPIOS.get(clave, frozenset())


def _calcular(usuario):
    if usuario.rol == Rol.ADMIN:
        return TODOS_ADMIN
    permisos = set(permisos_rol_sistema(usuario.rol))
    if usuario.rol == Rol.CLIENTE:
        return frozenset(permisos)
    for rol in usuario.roles_adicionales:
        permisos |= rol.permisos & set(ASIGNABLES)
    for ajuste in usuario.ajustes_permisos:
        if ajuste.permiso not in ASIGNABLES:
            continue
        if ajuste.concedido:
            permisos.add(ajuste.permiso)
        else:
            permisos.discard(ajuste.permiso)
    return frozenset(permisos)


def permisos_efectivos(usuario):
    """Los permisos de la cuenta. Se calculan una vez por peticion."""
    if not has_app_context() or usuario.id is None:
        return _calcular(usuario)
    cache = g.setdefault("_permisos_usuarios", {})
    if usuario.id not in cache:
        cache[usuario.id] = _calcular(usuario)
    return cache[usuario.id]


def olvidar_permisos():
    """Descarta lo calculado en esta peticion, tras cambiar roles o ajustes."""
    if has_app_context():
        g.pop("_permisos_usuarios", None)


# --------------------------------------------------------------------------
# Roles
# --------------------------------------------------------------------------

def asegurar_roles():
    """Crea los cuatro roles del sistema con su reparto por defecto si faltan.

    Idempotente; no hace commit. Devuelve los nombres que creo.
    """
    creados = []
    for clave, etiqueta in Rol.ETIQUETAS.items():
        if db.session.query(RolAcceso).filter_by(clave_sistema=clave).first() is not None:
            continue
        rol = RolAcceso(nombre=etiqueta, clave_sistema=clave, descripcion=DESCRIPCIONES_SISTEMA[clave])
        propios = PROPIOS.get(clave, frozenset())
        rol.asignaciones = [RolPermiso(permiso=p) for p in sorted(PERMISOS_POR_DEFECTO[clave] - propios)]
        db.session.add(rol)
        creados.append(etiqueta)
    db.session.flush()
    return creados


def roles():
    """Todos los roles: primero los del sistema, en el orden de Rol.ETIQUETAS."""
    orden = {clave: i for i, clave in enumerate(Rol.ETIQUETAS)}
    return sorted(db.session.query(RolAcceso).all(),
                  key=lambda r: (r.clave_sistema is None, orden.get(r.clave_sistema, 0), r.nombre.lower()))


def es_editable(rol):
    return not rol.es_sistema or rol.clave_sistema in ROLES_EDITABLES


def permisos_de(rol):
    """El reparto que se muestra de un rol, con lo propio de su tipo de cuenta."""
    if rol.clave_sistema is not None:
        return permisos_rol_sistema(rol.clave_sistema)
    return frozenset(rol.permisos & set(ASIGNABLES))


def usuarios_con_rol(rol):
    """Cuentas que tienen el rol: como principal (los del sistema) o como adicional."""
    from app.models import Usuario

    if rol.clave_sistema is not None:
        principales = db.session.query(Usuario).filter(Usuario.rol == rol.clave_sistema).all()
    else:
        principales = []
    return sorted({u.id: u for u in principales + list(rol.usuarios)}.values(), key=lambda u: u.nombre)


def _validar_permisos(v, permisos, minimo):
    permisos = set(permisos or ())
    for permiso in sorted(permisos):
        if permiso in SOLO_ADMIN:
            v.invalido("permisos", f"«{nombre(permiso)}» solo lo tienen los administradores: no se reparte.")
        elif any(permiso in propios for propios in PROPIOS.values()):
            v.invalido("permisos", f"«{nombre(permiso)}» depende del tipo de cuenta: no se reparte.")
        elif permiso not in PERMISOS:
            v.invalido("permisos", "Uno de los permisos elegidos no existe.")
    if minimo and not permisos:
        v.invalido("permisos", "Elija al menos un permiso.")
    return permisos & set(ASIGNABLES)


def validar_rol(datos, rol=None):
    """(nombre, descripcion, permisos) validados; lanza DatosInvalidos si no cumplen."""
    if rol is not None and not es_editable(rol):
        motivo = ("tiene todos los permisos." if rol.clave_sistema == Rol.ADMIN
                  else "solo da acceso al portal del cliente.")
        raise DatosInvalidos([(None, f"El rol {rol.nombre} no se edita: {motivo}")])
    v = Validador()
    if rol is not None and rol.es_sistema:
        nombre_rol = rol.nombre   # los del sistema no se renombran
    else:
        nombre_rol = v.texto(datos, "nombre", "el nombre del rol", LARGO_NOMBRE, requerido=True)
    descripcion = v.texto(datos, "descripcion", "la descripción", LARGO_DESCRIPCION)
    permisos = _validar_permisos(v, datos.get("permisos"), minimo=rol is None or not rol.es_sistema)
    if nombre_rol:
        clave = normalizar_texto(nombre_rol)
        for otro in db.session.query(RolAcceso):
            if otro is not rol and normalizar_texto(otro.nombre) == clave:
                v.invalido("nombre", f"Ya existe el rol «{otro.nombre}».")
    v.lanzar_si_hay()
    return nombre_rol, descripcion, permisos


def crear_rol(datos):
    nombre_rol, descripcion, permisos = validar_rol(datos)
    rol = RolAcceso(nombre=nombre_rol, descripcion=descripcion,
                    asignaciones=[RolPermiso(permiso=p) for p in sorted(permisos)])
    db.session.add(rol)
    db.session.flush()
    olvidar_permisos()
    return rol


def editar_rol(rol, datos):
    """Aplica los cambios. Devuelve {campo: (antes, despues)} para la auditoria."""
    nombre_rol, descripcion, permisos = validar_rol(datos, rol)
    antes = {"nombre": rol.nombre, "descripcion": rol.descripcion,
             "permisos": sorted(rol.permisos & set(ASIGNABLES))}
    rol.nombre, rol.descripcion = nombre_rol, descripcion
    actuales = {a.permiso: a for a in rol.asignaciones}
    rol.asignaciones = [actuales.get(p) or RolPermiso(permiso=p) for p in sorted(permisos)]
    despues = {"nombre": rol.nombre, "descripcion": rol.descripcion, "permisos": sorted(permisos)}
    olvidar_permisos()
    return {campo: (antes[campo], despues[campo]) for campo in antes if antes[campo] != despues[campo]}


def eliminar_rol(rol):
    """Borra un rol creado por el administrador. Devuelve los nombres de quienes lo tenian."""
    if rol.es_sistema:
        raise DatosInvalidos([(None, f"El rol {rol.nombre} es del sistema y no se elimina.")])
    afectados = [u.nombre for u in rol.usuarios]
    db.session.delete(rol)
    db.session.flush()
    olvidar_permisos()
    return afectados


# --------------------------------------------------------------------------
# Roles adicionales y ajustes de una cuenta
# --------------------------------------------------------------------------

def rechazo_permisos(usuario):
    """Por que la cuenta no lleva roles adicionales ni ajustes, o None."""
    if usuario.rol == Rol.ADMIN:
        return "Los administradores tienen todos los permisos."
    if usuario.rol == Rol.CLIENTE:
        return "Las cuentas de cliente solo ven su portal: no llevan roles adicionales ni permisos."
    return None


def roles_asignables(usuario):
    """Roles que se pueden sumar a la cuenta: los creados por el administrador y,
    si no lo es ya, el de gestor logistico."""
    if rechazo_permisos(usuario):
        return []
    return [r for r in roles()
            if (r.clave_sistema is None or r.clave_sistema == Rol.DESPACHADOR)
            and r.clave_sistema != usuario.rol]


def detalle_permisos(usuario):
    """Una fila por permiso, para la pantalla: de que roles le viene, su ajuste y si lo tiene."""
    efectivos = permisos_efectivos(usuario)
    ajustes = {a.permiso: a.concedido for a in usuario.ajustes_permisos}
    principal = (Rol.ETIQUETAS.get(usuario.rol, usuario.rol), permisos_rol_sistema(usuario.rol))
    fuentes = [principal] + [(r.nombre, permisos_de(r)) for r in usuario.roles_adicionales]
    filas = []
    for permiso, (texto, grupo) in PERMISOS.items():
        ajuste = ajustes.get(permiso)
        filas.append({
            "clave": permiso, "nombre": texto, "grupo": grupo,
            "por_roles": [nombre_rol for nombre_rol, permisos in fuentes if permiso in permisos],
            "ajuste": "" if ajuste is None else (AJUSTE_CONCEDER if ajuste else AJUSTE_DENEGAR),
            "asignable": permiso in ASIGNABLES,
            "tiene": permiso in efectivos,
        })
    return filas


def guardar_permisos_usuario(usuario, rol_ids, ajustes):
    """Fija los roles adicionales y los ajustes de la cuenta.

    `ajustes` es {permiso: "conceder" | "denegar" | ""}; los que no vienen
    quedan segun sus roles. Devuelve {campo: (antes, despues)} para la
    auditoria. Lanza DatosInvalidos si algo no se puede asignar.
    """
    rechazo = rechazo_permisos(usuario)
    if rechazo:
        raise DatosInvalidos([(None, rechazo)])
    disponibles = {r.id: r for r in roles_asignables(usuario)}
    v = Validador()
    if set(rol_ids) - set(disponibles):
        v.invalido("roles", "Uno de los roles elegidos no se puede asignar a esta cuenta.")
    nuevos = {}
    for permiso, valor in ajustes.items():
        if permiso not in ASIGNABLES:
            v.invalido("ajustes", (f"«{nombre(permiso)}» no se ajusta por cuenta."
                                   if permiso in PERMISOS else "Uno de los permisos no existe."))
        elif valor not in ("", AJUSTE_CONCEDER, AJUSTE_DENEGAR):
            v.invalido("ajustes", f"Ajuste no valido para «{nombre(permiso)}».")
        elif valor:
            nuevos[permiso] = valor == AJUSTE_CONCEDER
    v.lanzar_si_hay()

    antes_roles = sorted(r.nombre for r in usuario.roles_adicionales)
    antes_ajustes = {a.permiso: a.concedido for a in usuario.ajustes_permisos}
    usuario.roles_adicionales = [disponibles[i] for i in sorted(set(rol_ids))]
    actuales = {a.permiso: a for a in usuario.ajustes_permisos}
    for permiso, ajuste in actuales.items():
        if permiso in nuevos:
            ajuste.concedido = nuevos[permiso]
        else:
            usuario.ajustes_permisos.remove(ajuste)
    for permiso, concedido in sorted(nuevos.items()):
        if permiso not in actuales:
            usuario.ajustes_permisos.append(PermisoUsuario(permiso=permiso, concedido=concedido))
    olvidar_permisos()

    def legibles(mapa, concedido):
        return sorted(nombre(p) for p, c in mapa.items() if c is concedido)

    cambios = {}
    despues_roles = sorted(r.nombre for r in usuario.roles_adicionales)
    if antes_roles != despues_roles:
        cambios["roles"] = (antes_roles, despues_roles)
    for campo, concedido in (("concedidos", True), ("denegados", False)):
        antes, despues = legibles(antes_ajustes, concedido), legibles(nuevos, concedido)
        if antes != despues:
            cambios[campo] = (antes, despues)
    return cambios
