"""Registro en la auditoria y su aviso a operaciones.

`registrar_auditoria` agrega el registro a la sesion de la base y, si
`avisar`, encola el evento `accion_asistente` de Make; ambos se confirman con
el commit de quien llama, de modo que una accion revertida no deja registro
"exitoso". Con correo a operaciones (avisar=True): las acciones por voz, salvo
el flujo del conductor y las entradas de mercancia, y todo lo de Usuarios y
Configuracion. El resto de lo que se hace en pantalla queda en la auditoria
sin correo; llega a Google Sheets por /api/automatizacion/auditoria.

Las contrasenas y los tokens nunca llegan a la auditoria: cualquier dato cuya
clave los nombre se reemplaza por "(no se registra)" antes de guardar.
"""

import json

from app.extensions import db
from app.models import RESULTADO_ERROR, RESULTADO_EXITO, Auditoria
from app.services.notificaciones import (
    ORIGEN_AUTOMATICO,
    ROL_SISTEMA,
    aviso_accion_asistente,
    etiqueta_rol,
    origen_actual,
    usuario_actual,
)
from app.tiempo import ahora, iso_bogota

# Columnas de la hoja de auditoria (Google Sheets), en su orden.
COLUMNAS_HOJA = ("id_auditoria", "fecha_hora", "usuario", "rol", "origen", "accion", "entidad",
                 "entidad_id", "resumen", "detalle", "resultado", "call_id")

# Fragmentos de clave que marcan un dato sensible.
CLAVES_SENSIBLES = ("contrasena", "contraseña", "password", "clave", "token", "secret",
                    "api_key", "apikey", "firma", "signature")
OCULTO = "(no se registra)"
LARGO_RESUMEN = 500

__all__ = ["COLUMNAS_HOJA", "RESULTADO_ERROR", "RESULTADO_EXITO", "auditar_pantalla",
           "campos_cambiados", "fila_hoja", "limpiar", "registrar_auditoria"]


def _sensible(clave):
    clave = str(clave).lower()
    return any(fragmento in clave for fragmento in CLAVES_SENSIBLES)


def limpiar(datos):
    """Copia de `datos` sin contrasenas ni tokens (recorre dicts y listas)."""
    if isinstance(datos, dict):
        return {clave: (OCULTO if _sensible(clave) else limpiar(valor))
                for clave, valor in datos.items()}
    if isinstance(datos, (list, tuple)):
        return [limpiar(valor) for valor in datos]
    return datos


def registrar_auditoria(accion, entidad, resumen, detalle=None, *, resultado=RESULTADO_EXITO,
                        entidad_id=None, usuario=None, origen=None, call_id=None, avisar=True):
    """Agrega un registro de auditoria (y, si `avisar`, su evento) a la transaccion en curso.

    `usuario` y `origen` se toman de la peticion si no vienen: el usuario de la
    llamada de voz o el de la sesion web, y "voz" o "pantalla". Sin usuario el
    registro es del sistema, automatico.
    """
    usuario = usuario if usuario is not None else usuario_actual()
    origen = origen or origen_actual() or ORIGEN_AUTOMATICO
    registro = Auditoria(
        usuario_id=usuario.id if usuario else None,
        usuario_nombre=usuario.nombre if usuario else "Sistema SGDS",
        rol=usuario.rol if usuario else None,
        origen=origen if usuario else ORIGEN_AUTOMATICO,
        accion=accion[:80],
        entidad=entidad[:40],
        entidad_id=str(entidad_id)[:60] if entidad_id is not None else None,
        resumen=(resumen or accion)[:LARGO_RESUMEN],
        detalle=json.dumps(limpiar(detalle), ensure_ascii=False, default=str) if detalle else None,
        resultado=resultado,
        call_id=call_id,
        registrado_en=ahora(),
    )
    registro.usuario = usuario
    db.session.add(registro)
    # El flush le da su id, que viaja en el evento como id_auditoria.
    db.session.flush([registro])
    if avisar:
        aviso_accion_asistente(registro)
    return registro


def auditar_pantalla(accion, entidad, resumen, detalle=None, entidad_id=None):
    """Registra una accion de pantalla del alcance de la auditoria, sin correo.

    Clientes, sedes, productos, ajustes de inventario, vehiculos, rutas,
    pedidos y solicitudes. Usuarios y Configuracion usan `registrar_auditoria`
    con correo.
    """
    return registrar_auditoria(accion, entidad, resumen, detalle, entidad_id=entidad_id, avisar=False)


def campos_cambiados(cambios):
    """"nombre, telefono" a partir de {campo: (antes, despues)}."""
    return ", ".join(campo.replace("_", " ") for campo in cambios)


def fila_hoja(registro):
    """Valores del registro en el orden de COLUMNAS_HOJA."""
    return [
        registro.id,
        iso_bogota(registro.registrado_en),
        registro.usuario_nombre,
        etiqueta_rol(registro.rol) if registro.rol else ROL_SISTEMA,
        registro.origen,
        registro.accion,
        registro.entidad,
        registro.entidad_id or "",
        registro.resumen,
        registro.detalle_legible,
        registro.resultado,
        registro.call_id or "",
    ]


def cambios_legibles(cambios):
    """{campo: (antes, despues)} con valores que se pueden serializar."""
    return {campo: [_simple(antes), _simple(despues)] for campo, (antes, despues) in cambios.items()}


def _simple(valor):
    if valor is None or isinstance(valor, (str, int, float, bool)):
        return valor
    if hasattr(valor, "strftime"):
        return valor.strftime("%H:%M") if not hasattr(valor, "year") else valor.isoformat()
    return str(valor)
