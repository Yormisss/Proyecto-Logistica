"""Lectura de los resultados del pipeline de Big Data para el tablero.

La app no ejecuta Spark en una peticion web: lee lo que el pipeline deja en
`bigdata/resultados/` y lo prepara para graficar. El resultado se guarda en
memoria y solo se recalcula cuando el pipeline vuelve a escribir esos archivos,
de modo que el tablero no recorre el data lake en cada visita (RNF2).
"""

import json
import os
from datetime import date, timedelta
from pathlib import Path
from statistics import median

RAIZ = Path(__file__).resolve().parent.parent.parent / "bigdata"
RESULTADOS = RAIZ / "resultados"
LAKE = RAIZ / "datalake"

ARCHIVOS = ("gold.json", "calidad_datos.json", "ml_fallos.json", "streaming.json")
MESES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")
FRANJAS = (("MANANA", "Mañana"), ("MEDIODIA", "Mediodía"), ("TARDE", "Tarde"), ("NOCHE", "Noche"))
# Los datos generados van sin tildes: se corrigen solo al mostrarlos.
TILDES = {"Direccion errada": "Dirección errada", "Bogota": "Bogotá", "Chia": "Chía", "Cajica": "Cajicá"}
VARIABLES = {
    "zona_idx": "Zona",
    "distancia_km": "Distancia",
    "tasa_fallo_conductor": "Historial del conductor",
    "prioridad": "Prioridad",
    "hora_planeada": "Hora planeada",
    "ciudad_idx": "Ciudad",
    "conductor_id": "Conductor",
    "dia_semana": "Día de la semana",
    "n_items": "Numero de items",
    "sin_ventana": "Sin ventana horaria",
}
REGLAS = (
    ("ciudades_normalizadas", "Ciudades normalizadas"),
    ("duplicados_eliminados", "Duplicados eliminados"),
    ("prioridades_nulas", "Prioridades imputadas"),
    ("estados_en_minuscula", "Estados corregidos"),
    ("distancias_invalidas", "Distancias imputadas"),
    ("clientes_sin_nombre", "Clientes completados"),
)

_cache = {"firma": None, "datos": None}


def _leer(nombre):
    try:
        return json.loads((RESULTADOS / nombre).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _firma():
    marcas = []
    for nombre in ARCHIVOS:
        try:
            marcas.append((RESULTADOS / nombre).stat().st_mtime_ns)
        except OSError:
            marcas.append(None)
    return tuple(marcas)


def _mb(carpeta):
    total = 0
    for base, _, archivos in os.walk(carpeta):
        for a in archivos:
            try:
                total += (Path(base) / a).stat().st_size
            except OSError:
                pass
    return round(total / 1024 / 1024, 1)


def _tabla(gold, nombre):
    """Convierte una tabla gold en lista de diccionarios."""
    t = gold[nombre]
    return [dict(zip(t["columnas"], fila)) for fila in t["filas"]]


def _miles(n):
    return f"{n:,}".replace(",", ".")


def _etiquetas_mes(filas, totales):
    """Rotulos de eje ("ene 25") y de tooltip, que avisan si el mes esta incompleto."""
    cortas = [f"{MESES[f['mes'] - 1]} {str(f['anio'])[2:]}" for f in filas]
    largas = list(cortas)
    desde, hasta = date.fromisoformat(totales["desde"]), date.fromisoformat(totales["hasta"])
    if desde.day > 1:
        largas[0] += " (mes parcial)"
    if (hasta + timedelta(days=1)).day != 1:
        largas[-1] += " (mes parcial)"
    return cortas, largas


def _lineas(etiquetas, rotulos, series, minimo, maximo, pasos, sufijo=""):
    """Geometria de una grafica de lineas sobre un lienzo de 100 x 100.

    Todas las series comparten el mismo eje. Cada mes ocupa una banda vertical:
    el punto va en su centro y la banda completa es el area sensible del tooltip.
    """
    n = len(etiquetas)
    ancho = 100 / n
    rango = (maximo - minimo) or 1

    def y(valor):
        return round(100 - (valor - minimo) / rango * 100, 2)

    return {
        "series": [
            {
                "nombre": nombre,
                "color": color,
                "puntos": " ".join(f"{round((i + .5) * ancho, 2)},{y(v)}" for i, v in enumerate(valores)),
            }
            for nombre, color, valores in series
        ],
        "marcas": [
            {"texto": f"{_miles(round(minimo + rango * p / pasos))}{sufijo}", "pct": round(100 * p / pasos, 2)}
            for p in range(pasos + 1)
        ],
        "bandas": [
            {
                "x": round(i * ancho, 2),
                "ancho": round(ancho, 2),
                "tooltip": f"{rotulos[i]} — " + " · ".join(
                    f"{nombre}: {_miles(valores[i]) if isinstance(valores[i], int) else valores[i]}{sufijo}"
                    for nombre, _, valores in series
                ),
            }
            for i in range(n)
        ],
        # Una etiqueta de eje cada trimestre: 25 meses no caben rotulados uno a uno.
        "eje_x": [e if i % 3 == 0 else "" for i, e in enumerate(etiquetas)],
    }


def _pasos(celdas, clave):
    """Paso de 1 a 5 en la escala de color de un mapa de calor, del valor minimo al maximo."""
    valores = [c[clave] for c in celdas]
    minimo, maximo = min(valores), max(valores)
    for c in celdas:
        t = (c[clave] - minimo) / ((maximo - minimo) or 1)
        c["paso"] = min(4, int(5 * t)) + 1
        c["maximo"] = c[clave] == maximo


def _techo(valor, paso):
    return int(-(-valor // paso) * paso)


def _construir():
    gold, calidad = _leer("gold.json"), _leer("calidad_datos.json")
    if not gold or not calidad:
        return None

    # --- Volumen y data lake
    capas = [{"nombre": c.capitalize(), "mb": _mb(LAKE / c)} for c in ("raw", "bronze", "silver", "gold")]
    fuentes = sorted(
        (
            {"nombre": "Eventos de estado", "registros": calidad["eventos_salida"]},
            {"nombre": "Pedidos", "registros": calidad["filas_salida"]},
            {"nombre": "Movimientos de inventario", "registros": calidad["movimientos_salida"]},
        ),
        key=lambda f: -f["registros"],
    )
    reglas = sorted(
        ({"regla": texto, "filas": calidad.get(clave, 0)} for clave, texto in REGLAS),
        key=lambda r: -r["filas"],
    )

    # --- Series mensuales
    kpi = _tabla(gold, "kpi_mensual")
    etiquetas, rotulos = _etiquetas_mes(kpi, gold["totales"])
    meses = [
        {**k, "etiqueta": r, "eje": e if i % 3 == 0 else ""}
        for i, (k, e, r) in enumerate(zip(kpi, etiquetas, rotulos))
    ]
    piso = int(min(min(k["tasa_exito"], k["cumplimiento_ventana"]) for k in kpi) // 10 * 10)
    servicio = _lineas(
        etiquetas, rotulos,
        [
            ("Tasa de éxito", "var(--serie-1)", [k["tasa_exito"] for k in kpi]),
            ("Cumplimiento de ventana", "var(--serie-2)", [k["cumplimiento_ventana"] for k in kpi]),
        ],
        piso, 100, (100 - piso) // 10, "%",
    )
    demanda_mes = _tabla(gold, "demanda_mensual")
    tope = _techo(max(max(d["unidades_entrada"], d["unidades_salida"]) for d in demanda_mes), 20000)
    inventario = _lineas(
        *_etiquetas_mes(demanda_mes, gold["totales"]),
        [
            ("Unidades que entran", "var(--serie-1)", [d["unidades_entrada"] for d in demanda_mes]),
            ("Unidades que salen", "var(--serie-2)", [d["unidades_salida"] for d in demanda_mes]),
        ],
        0, tope, tope // 20000,
    )

    # --- Mapa de calor zona x franja
    celdas = {(f["zona"], f["franja"]): f for f in _tabla(gold, "fallos_zona_franja")}
    _pasos(list(celdas.values()), "tasa_fallo")
    por_zona = {}
    for (zona, _), c in celdas.items():
        z = por_zona.setdefault(zona, {"intentos": 0, "fallidos": 0})
        z["intentos"] += c["intentos"]
        z["fallidos"] += c["fallidos"]
    calor = []
    for zona, z in sorted(por_zona.items(), key=lambda p: -p[1]["fallidos"] / p[1]["intentos"]):
        calor.append({
            "zona": zona.capitalize(),
            "tasa": round(100 * z["fallidos"] / z["intentos"], 1),
            "celdas": [celdas.get((zona, clave)) for clave, _ in FRANJAS],
        })

    # --- Causas de fallo, de mayor a menor: las marginales se agrupan al final
    motivos = [
        {**m, "motivo": TILDES.get(m["motivo"], m["motivo"])}
        for m in sorted(_tabla(gold, "motivos_fallo"), key=lambda m: -m["porcentaje"])
    ]
    principales = [m for m in motivos if m["porcentaje"] >= 1]
    resto = [m for m in motivos if m["porcentaje"] < 1]
    if resto:
        principales.append({
            "motivo": "Otros",
            "casos": sum(m["casos"] for m in resto),
            "porcentaje": round(sum(m["porcentaje"] for m in resto), 2),
        })

    # --- Conductores: un punto por conductor sobre el eje de tasa de exito
    conductores = _tabla(gold, "rendimiento_conductor")
    tasas_c = [c["tasa_exito"] for c in conductores]
    c_min, c_max = int(min(tasas_c)), int(-(-max(tasas_c) // 1))
    mediana = round(median(tasas_c), 1)
    flota = {
        "puntos": [
            {
                "x": round((c["tasa_exito"] - c_min) / (c_max - c_min) * 100, 2),
                "carril": i % 5,
                "tooltip": f"Conductor #{c['conductor_id']} — {c['tasa_exito']}% de éxito · {_miles(c['intentos'])} intentos",
            }
            for i, c in enumerate(conductores)
        ],
        "marcas": [{"texto": f"{v}%", "pct": round((v - c_min) / (c_max - c_min) * 100, 2)} for v in range(c_min, c_max + 1)],
        "mediana": mediana,
        "mediana_pct": round((mediana - c_min) / (c_max - c_min) * 100, 2),
        "total": len(conductores),
    }

    # --- Inventario
    sku = _tabla(gold, "demanda_sku")
    abc = [
        {"clase": c, "productos": sum(1 for s in sku if s["clase_abc"] == c)}
        for c in "ABC"
    ]

    # --- Modelo predictivo
    ml = _leer("ml_fallos.json")
    if ml:
        m = ml["matriz_confusion"]
        ml["matriz"] = [
            [
                {"valor": m["verdaderos_positivos"], "texto": "Fallo detectado"},
                {"valor": m["falsos_negativos"], "texto": "Fallo no detectado"},
            ],
            [
                {"valor": m["falsos_positivos"], "texto": "Falsa alarma"},
                {"valor": m["verdaderos_negativos"], "texto": "Entrega confirmada"},
            ],
        ]
        _pasos([celda for fila in ml["matriz"] for celda in fila], "valor")
        ml["importancias"] = [
            {"variable": VARIABLES.get(v["variable"], v["variable"]), "pct": round(v["importancia"] * 100, 1)}
            for v in sorted(ml["importancia_variables"], key=lambda v: -v["importancia"])
            if v["importancia"] >= .01
        ]

    streaming = _leer("streaming.json")
    if streaming:
        streaming["estados"] = [
            {"estado": estado.replace("_", " ").capitalize(), "eventos": n}
            for estado, n in sorted(streaming["por_estado"], key=lambda p: -p[1])
        ]

    return {
        "totales": gold["totales"],
        "registros": sum(f["registros"] for f in fuentes),
        "fuentes": fuentes,
        "capas": capas,
        "lake_mb": round(sum(c["mb"] for c in capas), 1),
        "reglas": reglas,
        "corregidas": sum(r["filas"] for r in reglas),
        "meses": meses,
        "maximo_mes": max(k["entregados"] + k["fallidos"] for k in kpi),
        "servicio": servicio,
        "inventario": inventario,
        "franjas": [texto for _, texto in FRANJAS],
        "calor": calor,
        "motivos": principales,
        "ciudades": [{**c, "ciudad": TILDES.get(c["ciudad"], c["ciudad"])} for c in _tabla(gold, "fallos_ciudad")],
        "flota": flota,
        "sku": sku[:10],
        "abc": abc,
        "total_sku": len(sku),
        "ml": ml,
        "streaming": streaming,
    }


def panel():
    """Datos del tablero de Big Data, o None si el pipeline aun no ha corrido."""
    firma = _firma()
    if _cache["firma"] != firma:
        _cache["datos"] = _construir()
        _cache["firma"] = firma
    return _cache["datos"]
