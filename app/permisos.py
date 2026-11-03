"""RF1 / RNF5 - Catalogo de permisos y su reparto por defecto.

Un permiso es una seccion de la aplicacion con dos niveles: ver (consultar)
y gestionar (crear, editar, anular). Cada pantalla exige uno
(`requiere_permiso` en app/controllers/seguridad.py) y cada funcion de voz el
de su pantalla (`PERMISOS_VOZ`).

Los permisos de un usuario salen de su rol principal (`usuarios.rol`, que
dice que tipo de cuenta es), de sus roles adicionales y de sus ajustes
propios (conceder o denegar uno a uno); ver app/services/permisos.py. El
reparto de cada rol vive en la base y el administrador lo cambia en
Administracion > Roles; `PERMISOS_POR_DEFECTO` es el de una base nueva, igual
al que tenian los cuatro roles antes de que los permisos fueran configurables.

Tres grupos no se reparten:
- SOLO_ADMIN: gestionar usuarios, contrasenas y roles. Quien pudiera
  hacerlo podria darse a si mismo cualquier otro permiso o el rol de
  administrador, asi que solo lo tienen las cuentas de administrador.
- PROPIOS: la ruta del conductor y el portal del cliente dependen del tipo de
  cuenta (las rutas se asignan a conductores; el portal muestra los pedidos
  del cliente vinculado), no de un permiso que se pueda dar a cualquiera.
"""

from app.models.usuario import Rol

GRUPO_OPERACION = "Operación"
GRUPO_ADMINISTRACION = "Administración"
GRUPO_ACCESOS = "Accesos (solo administradores)"
GRUPO_PROPIOS = "Propios del tipo de cuenta"

# clave: (nombre, grupo). En el orden en que se muestran.
PERMISOS = {
    "tablero.ver": ("Ver el tablero", GRUPO_OPERACION),
    "resumen.enviar": ("Enviar el resumen del día", GRUPO_OPERACION),
    "pedidos.ver": ("Ver pedidos", GRUPO_OPERACION),
    "pedidos.gestionar": ("Gestionar pedidos (crear, editar, anular, importar)", GRUPO_OPERACION),
    "rutas.ver": ("Ver rutas", GRUPO_OPERACION),
    "rutas.gestionar": ("Gestionar rutas (crear, recalcular, quitar paradas, eliminar)", GRUPO_OPERACION),
    "inventario.ver": ("Ver inventario", GRUPO_OPERACION),
    "inventario.gestionar": ("Gestionar inventario (productos, entradas y ajustes)", GRUPO_OPERACION),
    "clientes.ver": ("Ver clientes y sedes", GRUPO_OPERACION),
    "clientes.gestionar": ("Gestionar clientes y sedes", GRUPO_OPERACION),
    "vehiculos.ver": ("Ver vehículos", GRUPO_OPERACION),
    "vehiculos.gestionar": ("Gestionar vehículos", GRUPO_OPERACION),
    "solicitudes.ver": ("Ver solicitudes de contacto", GRUPO_OPERACION),
    "solicitudes.atender": ("Atender solicitudes de contacto", GRUPO_OPERACION),
    "analitica.ver": ("Ver analítica de la operación", GRUPO_OPERACION),
    "bigdata.ver": ("Ver el panel de Big Data", GRUPO_OPERACION),
    "configuracion.gestionar": ("Gestionar la configuración", GRUPO_ADMINISTRACION),
    "bitacora.ver": ("Ver la bitácora", GRUPO_ADMINISTRACION),
    "errores.ver": ("Ver el registro de errores", GRUPO_ADMINISTRACION),
    "rendimiento.ver": ("Ver el rendimiento", GRUPO_ADMINISTRACION),
    "usuarios.gestionar": ("Gestionar usuarios y sus permisos", GRUPO_ACCESOS),
    "contrasenas.gestionar": ("Gestionar contraseñas", GRUPO_ACCESOS),
    "roles.gestionar": ("Gestionar roles", GRUPO_ACCESOS),
    "conductor.ruta": ("Mi ruta y entregas (conductor)", GRUPO_PROPIOS),
    "portal.cliente": ("Portal del cliente", GRUPO_PROPIOS),
}

SOLO_ADMIN = frozenset(p for p, (_, grupo) in PERMISOS.items() if grupo == GRUPO_ACCESOS)
PROPIOS = {Rol.CONDUCTOR: frozenset({"conductor.ruta"}), Rol.CLIENTE: frozenset({"portal.cliente"})}
# Los que se pueden repartir en roles y ajustes de usuario.
ASIGNABLES = tuple(p for p, (_, grupo) in PERMISOS.items()
                   if grupo in (GRUPO_OPERACION, GRUPO_ADMINISTRACION))

_OPERACION_GESTOR = frozenset(p for p, (_, grupo) in PERMISOS.items() if grupo == GRUPO_OPERACION)
# El administrador tiene todos menos los propios de otro tipo de cuenta: no
# recorre rutas ni ve el portal de un cliente, como antes de los permisos.
TODOS_ADMIN = frozenset(PERMISOS) - frozenset().union(*PROPIOS.values())
PERMISOS_POR_DEFECTO = {
    Rol.ADMIN: TODOS_ADMIN,
    Rol.DESPACHADOR: _OPERACION_GESTOR,
    Rol.CONDUCTOR: PROPIOS[Rol.CONDUCTOR],
    Rol.CLIENTE: PROPIOS[Rol.CLIENTE],
}

# Roles del sistema cuyo reparto el administrador puede cambiar. El del
# administrador es siempre todo, y el del cliente siempre solo su portal.
ROLES_EDITABLES = (Rol.DESPACHADOR, Rol.CONDUCTOR)

# Funcion de voz -> permiso, o (permiso para consultar, permiso para cambiar)
# en las que agrupan operaciones. Ademas del rol de su agente, cada funcion
# exige el permiso de la pantalla equivalente: quitarle un permiso a un rol
# tambien lo quita por voz.
PERMISOS_VOZ = {
    "admin/bitacora": "bitacora.ver",
    "admin/configuracion": "configuracion.gestionar",
    "admin/contrasenas": "contrasenas.gestionar",
    "admin/errores": "errores.ver",
    "admin/permisos": "usuarios.gestionar",
    "admin/resumen-rendimiento": "rendimiento.ver",
    "admin/roles": "roles.gestionar",
    "admin/usuarios": "usuarios.gestionar",
    "gestor/atender-solicitud": "solicitudes.atender",
    "gestor/avance-rutas": "rutas.ver",
    "gestor/buscar-pedido": "pedidos.ver",
    "gestor/causas-fallo": "analitica.ver",
    "gestor/clientes": ("clientes.ver", "clientes.gestionar"),
    "gestor/cumplimiento-ventana": "analitica.ver",
    "gestor/fallidos-hoy": "tablero.ver",
    "gestor/pedidos": "pedidos.gestionar",
    "gestor/pendientes-sin-ruta": "pedidos.ver",
    "gestor/productividad-conductores": "analitica.ver",
    "gestor/productos": ("inventario.ver", "inventario.gestionar"),
    "gestor/productos-bajo-minimo": "inventario.ver",
    "gestor/resumen-dia": "tablero.ver",
    "gestor/resumen-diario": ("tablero.ver", "resumen.enviar"),
    "gestor/rutas": ("rutas.ver", "rutas.gestionar"),
    "gestor/sedes": ("clientes.ver", "clientes.gestionar"),
    "gestor/solicitudes-contacto-pendientes": "solicitudes.ver",
    "gestor/stock-producto": "inventario.ver",
    "gestor/tiempo-promedio-entrega": "analitica.ver",
    "gestor/vehiculos": ("vehiculos.ver", "vehiculos.gestionar"),
}
# El agente del conductor y el del cliente son su tipo de cuenta.
ESPACIOS_PROPIOS = {"conductor": "conductor.ruta", "cliente": "portal.cliente"}


def nombre(permiso):
    return PERMISOS.get(permiso, (permiso, ""))[0]


def roles_por_defecto(permiso):
    """Roles del sistema que tienen `permiso` en una base nueva."""
    return tuple(rol for rol, permisos in PERMISOS_POR_DEFECTO.items() if permiso in permisos)


def permiso_voz(espacio, nombre_funcion, modifica):
    """El permiso que exige una funcion de voz, o None si no tiene uno asignado."""
    if espacio in ESPACIOS_PROPIOS:
        return ESPACIOS_PROPIOS[espacio]
    permiso = PERMISOS_VOZ.get(f"{espacio}/{nombre_funcion}")
    if isinstance(permiso, tuple):
        return permiso[1] if modifica else permiso[0]
    return permiso
