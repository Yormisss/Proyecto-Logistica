"""Indicadores de la operacion para el tablero de control.

Amplia el RF6 con los indicadores que el objetivo especifico pide para "facilitar
la toma de decisiones estrategicas", y calcula el cumplimiento del RNF2 a partir
de las mediciones reales de tiempo de respuesta.
"""

from datetime import timedelta

from sqlalchemy import case, func

from app.extensions import db
from app.tiempo import ahora, hoy
from app.models import (
    ENDPOINTS_CRITICOS,
    UMBRAL_MAXIMO_MS,
    UMBRAL_OBJETIVO_MS,
    EstadoPedido,
    EventoPedido,
    MedicionRendimiento,
    Pedido,
    PruebaEntrega,
    Rol,
    Ruta,
    Usuario,
)


# --------------------------------------------------------------------------
# Operacion
# --------------------------------------------------------------------------

def serie_entregas(dias=14, hasta=None):
    """Entregas cerradas por dia, separadas en exitosas y fallidas."""
    hasta = hasta or hoy()
    desde = hasta - timedelta(days=dias - 1)

    filas = (
        db.session.query(
            Pedido.fecha_despacho,
            func.sum(case((Pedido.estado == EstadoPedido.ENTREGADO, 1), else_=0)),
            func.sum(case((Pedido.estado == EstadoPedido.FALLIDO, 1), else_=0)),
        )
        .filter(Pedido.fecha_despacho.between(desde, hasta))
        .group_by(Pedido.fecha_despacho)
        .all()
    )
    indexado = {fila[0]: (int(fila[1] or 0), int(fila[2] or 0)) for fila in filas}

    serie = []
    for desplazamiento in range(dias):
        dia = desde + timedelta(days=desplazamiento)
        entregadas, fallidas = indexado.get(dia, (0, 0))
        serie.append({
            "fecha": dia,
            "entregadas": entregadas,
            "fallidas": fallidas,
            "total": entregadas + fallidas,
        })
    return serie


def motivos_fallo(dias=30, hasta=None):
    """Ranking de causas de entrega fallida (cadena causal 2.3.1)."""
    hasta = hasta or hoy()
    desde = hasta - timedelta(days=dias - 1)

    filas = (
        db.session.query(PruebaEntrega.motivo_fallo, func.count(PruebaEntrega.id))
        .join(Pedido, Pedido.id == PruebaEntrega.pedido_id)
        .filter(
            PruebaEntrega.motivo_fallo.isnot(None),
            Pedido.fecha_despacho.between(desde, hasta),
        )
        .group_by(PruebaEntrega.motivo_fallo)
        .order_by(func.count(PruebaEntrega.id).desc())
        .all()
    )
    return [{"motivo": motivo, "cantidad": int(cantidad)} for motivo, cantidad in filas]


def productividad_conductores(dias=30, hasta=None):
    """Entregas cerradas por conductor y su tasa de exito."""
    hasta = hasta or hoy()
    desde = hasta - timedelta(days=dias - 1)

    filas = (
        db.session.query(
            Usuario.id,
            Usuario.nombre,
            func.sum(case((Pedido.estado == EstadoPedido.ENTREGADO, 1), else_=0)),
            func.sum(case((Pedido.estado == EstadoPedido.FALLIDO, 1), else_=0)),
        )
        .join(Ruta, Ruta.conductor_id == Usuario.id)
        .join(Pedido, Pedido.ruta_id == Ruta.id)
        .filter(Usuario.rol == Rol.CONDUCTOR, Ruta.fecha.between(desde, hasta))
        .group_by(Usuario.id, Usuario.nombre)
        .all()
    )

    resultado = []
    for _, nombre, entregadas, fallidas in filas:
        entregadas, fallidas = int(entregadas or 0), int(fallidas or 0)
        cerradas = entregadas + fallidas
        resultado.append({
            "conductor": nombre,
            "entregadas": entregadas,
            "fallidas": fallidas,
            "cerradas": cerradas,
            "exito": round(entregadas * 100 / cerradas, 1) if cerradas else 0.0,
        })

    return sorted(resultado, key=lambda fila: fila["entregadas"], reverse=True)


def tiempo_promedio_entrega(dias=30, hasta=None):
    """Minutos transcurridos entre 'En ruta' y 'Entregado'.

    Es el indicador que mide directamente el problema de "tiempos de entrega
    ineficientes" planteado en el objetivo general.
    """
    hasta = hasta or hoy()
    desde = hasta - timedelta(days=dias - 1)

    salidas = (
        db.session.query(
            EventoPedido.pedido_id, func.max(EventoPedido.registrado_en).label("momento")
        )
        .filter(EventoPedido.estado_nuevo == EstadoPedido.EN_RUTA)
        .group_by(EventoPedido.pedido_id)
        .subquery()
    )
    llegadas = (
        db.session.query(
            EventoPedido.pedido_id, func.max(EventoPedido.registrado_en).label("momento")
        )
        .filter(EventoPedido.estado_nuevo == EstadoPedido.ENTREGADO)
        .group_by(EventoPedido.pedido_id)
        .subquery()
    )

    filas = (
        db.session.query(salidas.c.momento, llegadas.c.momento)
        .join(llegadas, llegadas.c.pedido_id == salidas.c.pedido_id)
        .join(Pedido, Pedido.id == salidas.c.pedido_id)
        .filter(Pedido.fecha_despacho.between(desde, hasta))
        .all()
    )

    duraciones = []
    for salida, llegada in filas:
        if salida and llegada and llegada > salida:
            duraciones.append((llegada - salida).total_seconds() / 60)

    if not duraciones:
        return {"promedio_min": None, "muestras": 0, "minimo": None, "maximo": None}

    return {
        "promedio_min": round(sum(duraciones) / len(duraciones), 1),
        "muestras": len(duraciones),
        "minimo": round(min(duraciones), 1),
        "maximo": round(max(duraciones), 1),
    }


def cumplimiento_ventana(dias=30, hasta=None):
    """Porcentaje de entregas realizadas dentro de la ventana horaria pactada."""
    hasta = hasta or hoy()
    desde = hasta - timedelta(days=dias - 1)

    filas = (
        db.session.query(Pedido.ventana_fin, PruebaEntrega.registrado_en)
        .join(PruebaEntrega, PruebaEntrega.pedido_id == Pedido.id)
        .filter(
            Pedido.estado == EstadoPedido.ENTREGADO,
            Pedido.ventana_fin.isnot(None),
            Pedido.fecha_despacho.between(desde, hasta),
        )
        .all()
    )

    if not filas:
        return {"porcentaje": None, "dentro": 0, "total": 0}

    dentro = sum(1 for ventana, momento in filas if momento and momento.time() <= ventana)
    return {
        "porcentaje": round(dentro * 100 / len(filas), 1),
        "dentro": dentro,
        "total": len(filas),
    }


# --------------------------------------------------------------------------
# RNF2 - Tiempos de respuesta
# --------------------------------------------------------------------------

def _percentil(valores_ordenados, fraccion):
    if not valores_ordenados:
        return None
    posicion = min(int(round(fraccion * (len(valores_ordenados) - 1))), len(valores_ordenados) - 1)
    return valores_ordenados[posicion]


def rendimiento(horas=24, solo_criticos=True):
    """Estadisticas de tiempo de respuesta por transaccion (RNF2)."""
    desde = ahora() - timedelta(hours=horas)

    consulta = db.session.query(
        MedicionRendimiento.endpoint, MedicionRendimiento.duracion_ms
    ).filter(MedicionRendimiento.registrado_en >= desde)

    if solo_criticos:
        consulta = consulta.filter(MedicionRendimiento.endpoint.in_(ENDPOINTS_CRITICOS))

    agrupado = {}
    for endpoint, duracion in consulta.all():
        agrupado.setdefault(endpoint, []).append(duracion)

    transacciones = []
    for endpoint, duraciones in agrupado.items():
        duraciones.sort()
        maximo = duraciones[-1]
        transacciones.append({
            "endpoint": endpoint,
            "nombre": ENDPOINTS_CRITICOS.get(endpoint, endpoint),
            "muestras": len(duraciones),
            "mediana_ms": round(_percentil(duraciones, 0.50), 1),
            "p95_ms": round(_percentil(duraciones, 0.95), 1),
            "maximo_ms": round(maximo, 1),
            "cumple": maximo <= UMBRAL_MAXIMO_MS,
            "dentro_objetivo": sum(1 for d in duraciones if d <= UMBRAL_OBJETIVO_MS),
        })

    transacciones.sort(key=lambda fila: fila["p95_ms"], reverse=True)

    todas = [d for duraciones in agrupado.values() for d in duraciones]
    todas.sort()

    return {
        "transacciones": transacciones,
        "muestras": len(todas),
        "mediana_ms": round(_percentil(todas, 0.50), 1) if todas else None,
        "p95_ms": round(_percentil(todas, 0.95), 1) if todas else None,
        "maximo_ms": round(todas[-1], 1) if todas else None,
        "cumplen": sum(1 for d in todas if d <= UMBRAL_MAXIMO_MS),
        "porcentaje_cumplimiento": (
            round(sum(1 for d in todas if d <= UMBRAL_MAXIMO_MS) * 100 / len(todas), 2)
            if todas else None
        ),
        "umbral_objetivo_ms": UMBRAL_OBJETIVO_MS,
        "umbral_maximo_ms": UMBRAL_MAXIMO_MS,
        "horas": horas,
    }
