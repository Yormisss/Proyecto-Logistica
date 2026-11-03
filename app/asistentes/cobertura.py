"""Cobertura por voz de cada accion de pantalla.

Todo lo que un usuario puede hacer en pantalla con su rol tambien se puede
hacer por voz con Gaby. Este mapa dice, para cada ruta POST de la pantalla,
que funcion del asistente la cubre, o por que queda excluida. La prueba de
cobertura falla si aparece una ruta POST que no este aqui, si una funcion
nombrada no existe o no la pueden usar los mismos roles, y si se excluye algo
fuera de la lista aprobada.

Cada funcion se nombra "espacio/nombre", o "espacio/nombre:operacion" si la
funcion agrupa varias operaciones de una entidad con el parametro `operacion`.

`pendiente` marca lo que aun falta por voz y en que fase se hace. Solo se
admite con PENDIENTES_PERMITIDOS en True. Desde el cierre de la fase 4 la
cobertura esta completa y la bandera queda en False: una ruta POST nueva de la
pantalla debe llegar con su funcion de voz o con una exclusion aprobada.
"""

from dataclasses import dataclass

PENDIENTES_PERMITIDOS = False


@dataclass(frozen=True)
class Cobertura:
    voz: tuple = ()
    excluida: str = ""
    pendiente: str = ""


# Exclusiones aprobadas: no se agrega ninguna sin preguntar.
SUBIR_ARCHIVOS = ("Subir o revisar archivos: importar pedidos exige elegir un archivo CSV y revisar su "
                  "contenido en pantalla; no tiene equivalente hablado.")
CONTRASENA_DICTADA = ("Escribir una contraseña: las contraseñas nunca se dicen por voz. Por voz, "
                      "restablecer-contrasena genera una temporal y la envía al correo de la cuenta.")

COBERTURA = {
    # --- Fuera del alcance por voz (aprobado) ---
    "auth.login": Cobertura(excluida="Iniciar sesión: la llamada de voz parte de una sesión ya iniciada."),
    "auth.perfil": Cobertura(excluida="Cambiar la propia contraseña: las contraseñas nunca se dicen por voz."),
    # Aprobadas al integrar la seguridad de juan/seguridad-evaluacion (02/11/2026).
    "auth.recuperar": Cobertura(excluida="Olvidé mi contraseña: es para quien no puede iniciar sesión, "
                                         "y la llamada de voz parte de una sesión ya iniciada."),
    "auth.restablecer": Cobertura(excluida="Definir la contraseña con el enlace del correo: las "
                                           "contraseñas nunca se dicen por voz."),
    "auth.cambiar_correo": Cobertura(excluida="Cambiar el propio correo de inicio de sesión: es la "
                                              "identidad de la cuenta, como la contraseña, y pide la "
                                              "contraseña actual."),
    "usuarios.contrasena": Cobertura(excluida=CONTRASENA_DICTADA),
    "errores.probar": Cobertura(excluida="Provocar un error a propósito es una herramienta de "
                                         "diagnóstico de pantalla; por voz solo ensuciaría el registro."),
    "asistente.llamada": Cobertura(excluida="Es el botón que inicia la propia llamada de voz."),
    "pedidos.importar": Cobertura(excluida=SUBIR_ARCHIVOS),
    "pedidos.confirmar_importacion": Cobertura(excluida=SUBIR_ARCHIVOS),

    # --- Conductor ---
    "conductor.marcar_en_ruta": Cobertura(voz=("conductor/parada:en-camino",)),
    "conductor.reintentar": Cobertura(voz=("conductor/parada:en-camino",)),
    "conductor.fallar": Cobertura(voz=("conductor/parada:fallo",)),
    "conductor.entregar": Cobertura(voz=("conductor/parada:entregar",)),
    "conductor.iniciar": Cobertura(voz=("conductor/iniciar-ruta",)),

    # --- Tablero ---
    "admin.enviar_resumen": Cobertura(voz=("gestor/resumen-diario:enviar",)),

    # --- Pedidos ---
    "pedidos.nuevo": Cobertura(voz=("gestor/pedidos:crear",)),
    "pedidos.anular": Cobertura(voz=("gestor/pedidos:anular",)),
    "pedidos.editar": Cobertura(voz=("gestor/pedidos:editar",)),

    # --- Inventario ---
    "inventario.detalle": Cobertura(voz=("gestor/productos:entrada", "gestor/productos:ajuste")),
    "inventario.nuevo": Cobertura(voz=("gestor/productos:crear",)),
    "inventario.editar": Cobertura(voz=("gestor/productos:editar", "gestor/productos:activar",
                                        "gestor/productos:desactivar")),

    # --- Rutas ---
    "rutas.nueva": Cobertura(voz=("gestor/rutas:crear",)),
    "rutas.recalcular": Cobertura(voz=("gestor/rutas:recalcular",)),
    "rutas.quitar_parada": Cobertura(voz=("gestor/rutas:quitar-parada",)),
    "rutas.eliminar": Cobertura(voz=("gestor/rutas:eliminar",)),

    # --- Clientes y sedes ---
    "clientes_admin.nuevo": Cobertura(voz=("gestor/clientes:crear",)),
    "clientes_admin.editar": Cobertura(voz=("gestor/clientes:editar", "gestor/clientes:activar",
                                            "gestor/clientes:desactivar")),
    "clientes_admin.detalle": Cobertura(voz=("gestor/sedes:crear",)),
    "clientes_admin.editar_sede_vista": Cobertura(voz=("gestor/sedes:editar",)),
    "clientes_admin.alternar_sede": Cobertura(voz=("gestor/sedes:activar", "gestor/sedes:desactivar")),

    # --- Solicitudes de contacto ---
    "solicitudes.atender": Cobertura(voz=("gestor/atender-solicitud",)),

    # --- Vehiculos ---
    "vehiculos.nuevo": Cobertura(voz=("gestor/vehiculos:crear",)),
    "vehiculos.editar": Cobertura(voz=("gestor/vehiculos:editar",)),
    "vehiculos.alternar": Cobertura(voz=("gestor/vehiculos:activar", "gestor/vehiculos:desactivar")),

    # --- Usuarios (administrador) ---
    # El correo de inicio de sesion de una cuenta existente se cambia solo en pantalla.
    "usuarios.nuevo": Cobertura(voz=("admin/usuarios:crear",)),
    "usuarios.editar": Cobertura(voz=("admin/usuarios:editar", "admin/usuarios:activar",
                                      "admin/usuarios:desactivar")),
    "usuarios.alternar_estado": Cobertura(voz=("admin/usuarios:activar", "admin/usuarios:desactivar")),
    "usuarios.restablecer": Cobertura(voz=("admin/usuarios:restablecer-contrasena",)),
    "usuarios.permisos": Cobertura(voz=("admin/permisos:asignar-rol", "admin/permisos:quitar-rol",
                                        "admin/permisos:conceder", "admin/permisos:denegar",
                                        "admin/permisos:quitar-ajuste")),
    "usuarios.alternar_exigencia": Cobertura(voz=("admin/contrasenas:exigir-cambio",
                                                  "admin/contrasenas:quitar-exigencia")),
    "usuarios.exigir_cambio_masivo": Cobertura(voz=("admin/contrasenas:exigir-cambio-masivo",)),

    # --- Roles (administrador) ---
    "roles.nuevo": Cobertura(voz=("admin/roles:crear",)),
    "roles.editar": Cobertura(voz=("admin/roles:editar", "admin/roles:agregar-permiso",
                                   "admin/roles:quitar-permiso")),
    "roles.eliminar": Cobertura(voz=("admin/roles:eliminar",)),

    # --- Configuracion del negocio (administrador) ---
    "configuracion.centro": Cobertura(voz=("admin/configuracion:editar-centro",)),
    "configuracion.sesion": Cobertura(voz=("admin/configuracion:editar-inactividad",)),
    "configuracion.agregar_motivo": Cobertura(voz=("admin/configuracion:agregar-motivo",)),
    "configuracion.renombrar_motivo": Cobertura(voz=("admin/configuracion:renombrar-motivo",)),
    "configuracion.alternar_motivo": Cobertura(voz=("admin/configuracion:activar-motivo",
                                                    "admin/configuracion:desactivar-motivo")),
}

# Blueprints que no son pantallas: los llaman Retell y Make, no un usuario.
BLUEPRINTS_SIN_PANTALLA = ("asistente_api", "automatizacion_api", "static")


def rutas_post_de_pantalla(app):
    """{endpoint: regla} de cada ruta POST que usa una persona en pantalla."""
    return {
        regla.endpoint: regla.rule
        for regla in app.url_map.iter_rules()
        if "POST" in regla.methods and regla.endpoint.split(".")[0] not in BLUEPRINTS_SIN_PANTALLA
    }


def separar(referencia):
    """"admin/usuarios:crear" -> ("admin", "usuarios", "crear")."""
    ruta, _, operacion = referencia.partition(":")
    espacio, _, nombre = ruta.partition("/")
    return espacio, nombre, operacion or None
