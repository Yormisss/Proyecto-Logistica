"""Fuente 1: base de datos transaccional de la aplicacion (SQLite/MySQL).

Exporta los pedidos reales de la app a CSV con el mismo esquema que el ERP, junto
con su bitacora de eventos. Es la extraccion desde el sistema OLTP; la capa de
ingesta los lleva despues al lake junto con las demas fuentes.
"""

import csv
import json
import sys
from pathlib import Path

RAIZ_PROYECTO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ_PROYECTO))

import entorno  # noqa: E402
from fuente_generador import COLUMNAS  # noqa: E402


def _local(uri):
    if "://" in uri and not uri.startswith("file:"):
        raise SystemExit("La exportacion de la app escribe en disco local; copie luego a HDFS con `hdfs dfs -put`.")
    return Path(uri.replace("file:///", ""))


def exportar():
    from app import crear_app
    from app.extensions import db
    from app.models import EstadoPedido, EventoPedido, Pedido

    app = crear_app("desarrollo")
    with app.app_context():
        destino = _local(entorno.ruta("raw", "app_pedidos"))
        destino.mkdir(parents=True, exist_ok=True)
        with open(destino / "pedidos.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(COLUMNAS)
            n = 0
            for p in db.session.query(Pedido).yield_per(500):
                entrega = max((e.registrado_en for e in p.eventos if e.estado_nuevo in EstadoPedido.FINALES),
                              default=None)
                w.writerow([
                    f"APP-{p.codigo}", p.cliente_id, p.cliente_nombre, p.ciudad, p.latitud, p.longitud,
                    p.fecha_despacho, p.prioridad, p.ventana_inicio.strftime("%H:%M") if p.ventana_inicio else "",
                    p.ventana_fin.strftime("%H:%M") if p.ventana_fin else "", p.estado,
                    p.ruta.conductor_id if p.ruta else "", p.ruta.codigo if p.ruta else "",
                    p.ruta.distancia_km if p.ruta else "", sum(i.cantidad for i in p.items),
                    p.creado_en, entrega or "",
                    p.prueba_entrega.motivo_fallo if p.prueba_entrega else "",
                ])
                n += 1

        destino_ev = _local(entorno.ruta("raw", "app_eventos"))
        destino_ev.mkdir(parents=True, exist_ok=True)
        with open(destino_ev / "eventos_app.json", "w", encoding="utf-8") as f:
            for e in db.session.query(EventoPedido).join(Pedido).yield_per(500):
                f.write(json.dumps({
                    "codigo": f"APP-{e.pedido.codigo}",
                    "conductor_id": e.pedido.ruta.conductor_id if e.pedido.ruta else None,
                    "latitud": e.latitud or e.pedido.latitud, "longitud": e.longitud or e.pedido.longitud,
                    "estado_anterior": e.estado_anterior, "estado_nuevo": e.estado_nuevo,
                    "registrado_en": e.registrado_en.strftime("%Y-%m-%d %H:%M:%S"),
                }) + "\n")
    print(f"Exportados {n} pedidos reales de la app a {destino}")


if __name__ == "__main__":
    exportar()
