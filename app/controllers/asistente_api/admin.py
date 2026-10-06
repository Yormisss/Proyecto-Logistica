"""Funciones del asistente de voz del administrador.

El admin usa ademas todas las del gestor (gestor.py las declara para ambos
roles). Aqui van las consultas de analitica, con los mismos periodos que el
panel, y el ajuste de inventario a un valor absoluto, que el gestor no tiene
por voz.

Fuera del alcance por voz: usuarios, roles, contrasenas y activacion de
cuentas. No hay funciones para eso; el prompt del agente indica que se hace
en pantalla.
"""

from flask import g

from app.controllers.asistente_api import (
    NOTA_ASISTENTE, cantidad, exigir_confirmacion, funcion_asistente, responder,
)
from app.controllers.asistente_api.comun import (
    LARGO_MAXIMO_MOTIVO, entero, enumerar, porcentaje, resolver, texto,
)
from app.extensions import db
from app.models import Rol, TipoMovimiento
from app.services import analitica
from app.services.busqueda_voz import buscar_productos
from app.services.inventario import registrar_movimiento

ESPACIO = "admin"
ROLES = (Rol.ADMIN,)

# Mismos periodos que el panel de analitica y el de rendimiento.
PERIODOS_DIAS = (7, 14, 30)
DIAS_POR_DEFECTO = 14
PERIODOS_HORAS = (1, 24, 168)
HORAS_POR_DEFECTO = 24

LIMITE_LISTA = 5

PARAMETRO_DIAS = {
    "dias": {"type": "integer",
             "description": "Periodo en dias: 7, 14 (por defecto) o 30, como en el panel."},
}


def _dias():
    dias = entero(g.argumentos.get("dias"))
    return dias if dias in PERIODOS_DIAS else DIAS_POR_DEFECTO


def _minutos(valor):
    return f"{valor:.0f} minutos" if valor is not None else "sin datos"


@funcion_asistente(
    ESPACIO, "tiempo-promedio-entrega", roles=ROLES,
    descripcion="Tiempo promedio desde que una parada sale en camino hasta que se entrega.",
    parametros=PARAMETRO_DIAS,
)
def tiempo_promedio_entrega():
    dias = _dias()
    datos = analitica.tiempo_promedio_entrega(dias)
    if not datos["muestras"]:
        return responder(f"En los últimos {dias} días no hay entregas con tiempos registrados.")
    return responder(
        f"En los últimos {dias} días, una entrega tarda en promedio "
        f"{_minutos(datos['promedio_min'])} desde que sale en camino, sobre "
        f"{cantidad(datos['muestras'], 'entrega')}; la más rápida "
        f"{_minutos(datos['minimo'])} y la más lenta {_minutos(datos['maximo'])}."
    )


@funcion_asistente(
    ESPACIO, "cumplimiento-ventana", roles=ROLES,
    descripcion="Porcentaje de entregas hechas dentro de la ventana horaria pactada.",
    parametros=PARAMETRO_DIAS,
)
def cumplimiento_ventana():
    dias = _dias()
    datos = analitica.cumplimiento_ventana(dias)
    if not datos["total"]:
        return responder(f"En los últimos {dias} días no hay entregas con ventana horaria.")
    return responder(
        f"En los últimos {dias} días, {porcentaje(datos['porcentaje'])} de las entregas con "
        f"ventana se hizo a tiempo: {datos['dentro']} de {datos['total']}."
    )


@funcion_asistente(
    ESPACIO, "productividad-conductores", roles=ROLES,
    descripcion="Entregas y tasa de exito por conductor, del mas productivo al menos.",
    parametros=PARAMETRO_DIAS,
)
def productividad_conductores():
    dias = _dias()
    filas = analitica.productividad_conductores(dias)
    if not filas:
        return responder(f"En los últimos {dias} días no hay entregas cerradas por conductores.")
    return responder(
        f"En los últimos {dias} días: "
        + enumerar([
            f"{f['conductor']}, {cantidad(f['entregadas'], 'entregada')} y "
            f"{cantidad(f['fallidas'], 'fallida')}, éxito {porcentaje(f['exito'])}"
            for f in filas[:LIMITE_LISTA]
        ])
        + "."
    )


@funcion_asistente(
    ESPACIO, "causas-fallo", roles=ROLES,
    descripcion="Motivos de entrega fallida, del mas frecuente al menos.",
    parametros=PARAMETRO_DIAS,
)
def causas_fallo():
    dias = _dias()
    filas = analitica.motivos_fallo(dias)
    if not filas:
        return responder(f"En los últimos {dias} días no hay entregas fallidas con motivo.")
    total = sum(f["cantidad"] for f in filas)
    return responder(
        f"En los últimos {dias} días hubo {cantidad(total, 'entrega fallida', 'entregas fallidas')}. "
        "Las causas principales: "
        + enumerar([f"{f['motivo']}, {f['cantidad']}" for f in filas[:LIMITE_LISTA]])
        + "."
    )


@funcion_asistente(
    ESPACIO, "resumen-rendimiento", roles=ROLES,
    descripcion="Resumen del panel de rendimiento: tiempos de respuesta de las transacciones "
                "criticas y su cumplimiento del limite.",
    parametros={"horas": {"type": "integer",
                          "description": "Ventana en horas: 1, 24 (por defecto) o 168."}},
)
def resumen_rendimiento():
    horas = entero(g.argumentos.get("horas"))
    horas = horas if horas in PERIODOS_HORAS else HORAS_POR_DEFECTO
    datos = analitica.rendimiento(horas)
    if not datos["muestras"]:
        return responder(f"No hay mediciones de rendimiento en las últimas {horas} horas.")

    partes = [
        f"En las últimas {horas} horas hay {cantidad(datos['muestras'], 'medición', 'mediciones')}: "
        f"mediana de {datos['mediana_ms']:.0f} milisegundos, percentil 95 de "
        f"{datos['p95_ms']:.0f} y máximo de {datos['maximo_ms']:.0f}. "
        f"Cumple el límite el {porcentaje(datos['porcentaje_cumplimiento'])}."
    ]
    lentas = [t["nombre"] for t in datos["transacciones"] if not t["cumple"]]
    if lentas:
        partes.append("Superan el límite: " + enumerar(lentas[:LIMITE_LISTA]) + ".")
    return responder(" ".join(partes))


@funcion_asistente(
    ESPACIO, "ajustar-inventario", roles=ROLES, accion=True,
    descripcion="Ajusta el stock de un producto a un valor absoluto (inventario fisico), con "
                "motivo obligatorio. Puede disparar el aviso de stock bajo.",
    parametros={
        "producto": {"type": "string", "description": "SKU o nombre del producto."},
        "valor": {"type": "integer", "description": "Nuevo stock, entero mayor o igual a cero."},
        "motivo": {"type": "string", "description": "Motivo del ajuste."},
    },
    requeridos=("producto", "valor", "motivo"),
)
def ajustar_inventario():
    buscado = texto("producto")
    if not buscado:
        return responder("Necesito el SKU o el nombre del producto.")
    producto, error = resolver(
        buscar_productos(buscado, solo_activos=False), lambda p: f"{p.sku}, {p.nombre}",
        "productos", f"No encontré el producto {buscado}.",
    )
    if producto is None:
        return responder(error)
    valor = entero(g.argumentos.get("valor"))
    if valor is None or valor < 0:
        return responder("El nuevo stock debe ser un número entero mayor o igual a cero.")
    motivo = texto("motivo")[:LARGO_MAXIMO_MOTIVO]
    if not motivo:
        return responder("Necesito el motivo del ajuste.")
    if valor == producto.stock_actual:
        return responder(f"El stock de {producto.sku} ya es {valor}; no hay nada que ajustar.")

    pendiente = exigir_confirmacion(
        f"Voy a ajustar el inventario de {producto.sku}, {producto.nombre}: el stock pasa de "
        f"{producto.stock_actual} a {valor}, por: {motivo}."
    )
    if pendiente:
        return pendiente

    registrar_movimiento(
        producto, TipoMovimiento.AJUSTE, valor, g.usuario.id,
        motivo=f"{motivo}. {NOTA_ASISTENTE}",
    )
    db.session.commit()
    return responder(f"Listo, el stock de {producto.sku} quedó en {valor}.")
