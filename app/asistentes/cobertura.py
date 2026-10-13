"""Cobertura por voz de cada accion de pantalla.

Todo lo que un usuario puede hacer en pantalla con su rol tambien se puede
hacer por voz con Gaby. Este mapa dice, para cada ruta POST de la pantalla,
que funcion del asistente la cubre, o por que queda excluida. La prueba de
cobertura falla si aparece una ruta POST que no este aqui, si una funcion
nombrada no existe o no la pueden usar los mismos roles, y si se excluye algo
fuera de la lista aprobada.

Cada funcion se nombra "espacio/nombre", o "espacio/nombre:operacion" si la
funcion agrupa varias operaciones de una entidad con el parametro `operacion`.

`pendiente` marca lo que aun falta por voz y en que fase se hace. Se admite
mientras PENDIENTES_PERMITIDOS sea True; al cerrar la fase 5 debe quedar vacio.
"""

from dataclasses import dataclass

PENDIENTES_PERMITIDOS = True


@dataclass(frozen=True)
class Cobertura:
    voz: tuple = ()
    excluida: str = ""
    pendiente: str = ""


# Exclusiones aprobadas: no se agrega ninguna sin preguntar.
IMPORTAR_CSV = "Importar pedidos exige subir un archivo CSV; no tiene equivalente hablado."

COBERTURA = {
    # --- Fuera del alcance por voz (aprobado) ---
    "auth.login": Cobertura(excluida="Iniciar sesión: la llamada de voz parte de una sesión ya iniciada."),
    "auth.perfil": Cobertura(excluida="Cambiar la propia contraseña: las contraseñas nunca se dicen por voz."),
    "asistente.llamada": Cobertura(excluida="Es el botón que inicia la propia llamada de voz."),
    "pedidos.importar": Cobertura(excluida=IMPORTAR_CSV),
    "pedidos.confirmar_importacion": Cobertura(excluida=IMPORTAR_CSV),

    # --- Conductor ---
    "conductor.marcar_en_ruta": Cobertura(voz=("conductor/parada:en-camino",)),
    "conductor.reintentar": Cobertura(voz=("conductor/parada:en-camino",)),
    "conductor.fallar": Cobertura(voz=("conductor/parada:fallo",)),
    "conductor.entregar": Cobertura(pendiente="Fase 4: confirmar la entrega con nombre y documento de quien recibe."),
    "conductor.iniciar": Cobertura(pendiente="Fase 4: iniciar la ruta del día."),

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
    "solicitudes.atender": Cobertura(pendiente="Fase 4: marcar una solicitud como atendida."),

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

    # --- Configuracion del negocio (administrador) ---
    "configuracion.centro": Cobertura(voz=("admin/configuracion:editar-centro",)),
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
