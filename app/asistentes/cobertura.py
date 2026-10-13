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
    "conductor.marcar_en_ruta": Cobertura(voz=("conductor/marcar-en-camino",)),
    "conductor.reintentar": Cobertura(voz=("conductor/marcar-en-camino",)),
    "conductor.fallar": Cobertura(voz=("conductor/registrar-fallo",)),
    "conductor.entregar": Cobertura(pendiente="Fase 4: confirmar la entrega con nombre y documento de quien recibe."),
    "conductor.iniciar": Cobertura(pendiente="Fase 4: iniciar la ruta del día."),

    # --- Pedidos ---
    "pedidos.nuevo": Cobertura(voz=("gestor/crear-pedido",)),
    "pedidos.anular": Cobertura(voz=("gestor/anular-pedido",)),
    "pedidos.editar": Cobertura(voz=("gestor/cambiar-prioridad",),
                                pendiente="Fase 4: editar fecha, sede, ventana, observaciones y productos."),

    # --- Inventario ---
    "inventario.detalle": Cobertura(voz=("gestor/registrar-entrada", "admin/ajustar-inventario"),
                                    pendiente="Fase 4: ajuste de inventario por voz también para el gestor."),
    "inventario.nuevo": Cobertura(pendiente="Fase 3: crear productos."),
    "inventario.editar": Cobertura(pendiente="Fase 3: editar y activar o desactivar productos."),

    # --- Rutas ---
    "rutas.nueva": Cobertura(pendiente="Fase 4: crear rutas."),
    "rutas.recalcular": Cobertura(pendiente="Fase 4: recalcular la secuencia de una ruta."),
    "rutas.quitar_parada": Cobertura(pendiente="Fase 4: quitar una parada de una ruta."),
    "rutas.eliminar": Cobertura(pendiente="Fase 4: eliminar una ruta."),

    # --- Clientes y sedes ---
    "clientes_admin.nuevo": Cobertura(pendiente="Fase 3: crear clientes."),
    "clientes_admin.editar": Cobertura(pendiente="Fase 3: editar clientes y registrar el NIT una vez."),
    "clientes_admin.detalle": Cobertura(pendiente="Fase 3: agregar sedes."),
    "clientes_admin.editar_sede_vista": Cobertura(pendiente="Fase 3: editar sedes."),
    "clientes_admin.alternar_sede": Cobertura(pendiente="Fase 3: activar o desactivar sedes."),

    # --- Solicitudes de contacto ---
    "solicitudes.atender": Cobertura(pendiente="Fase 4: marcar una solicitud como atendida."),

    # --- Vehiculos ---
    "vehiculos.nuevo": Cobertura(pendiente="Fases 3 y 4: crear vehículos."),
    "vehiculos.editar": Cobertura(pendiente="Fases 3 y 4: editar vehículos."),
    "vehiculos.alternar": Cobertura(pendiente="Fases 3 y 4: activar o desactivar vehículos."),

    # --- Usuarios (administrador) ---
    "usuarios.nuevo": Cobertura(pendiente="Fase 3: crear usuarios y habilitar el portal."),
    "usuarios.editar": Cobertura(pendiente="Fase 3: editar nombre y rol."),
    "usuarios.alternar_estado": Cobertura(pendiente="Fase 3: activar o desactivar usuarios."),
    "usuarios.restablecer": Cobertura(pendiente="Fase 3: restablecer la contraseña (llega por correo)."),

    # --- Configuracion del negocio (administrador) ---
    "configuracion.centro": Cobertura(pendiente="Fase 3: editar el centro de distribución."),
    "configuracion.agregar_motivo": Cobertura(pendiente="Fase 3: agregar motivos de fallo."),
    "configuracion.renombrar_motivo": Cobertura(pendiente="Fase 3: renombrar motivos de fallo."),
    "configuracion.alternar_motivo": Cobertura(pendiente="Fase 3: activar o desactivar motivos de fallo."),
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
