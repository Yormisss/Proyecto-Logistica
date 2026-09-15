"""Ejecuta todas las suites de prueba del sistema.

    .venv/bin/python pruebas/ejecutar_todas.py

Cada suite reinicia la base de datos de desarrollo y vuelve a sembrarla, de modo
que los resultados son reproducibles. Requiere conexion a internet para la suite
de ruteo (consulta el servicio OSRM); sin conexion esa suite verifica igualmente
el algoritmo local de respaldo.
"""

import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _preparar import RAIZ, PYTHON, reiniciar_base  # noqa: E402

SUITES = [
    ("prueba_01_acceso.py", "RF1 · Autenticacion, roles y tablero"),
    ("prueba_02_pedidos_csv.py", "RF2 · Pedidos manuales, CSV e inventario"),
    ("prueba_03_ruteo.py", "RF3 · Servicio de ruteo y respaldo local"),
    ("prueba_04_rutas_web.py", "RF3 · Planificacion de rutas y mapa"),
    ("prueba_05_entrega_inventario.py", "RF4/RF5 · Entrega en terreno e inventario"),
    ("prueba_06_analitica_rendimiento.py", "RF6/RNF2 · Analitica y tiempos de respuesta"),
    ("prueba_07_clientes_portal.py", "RF1/RF2 · Clientes normalizados y portal"),
    ("prueba_08_administracion.py", "RF1 · Administracion de cuentas y clientes"),
]


def main():
    resultados = []

    for archivo, descripcion in SUITES:
        print(f"\n{'=' * 70}\n{descripcion}\n{'=' * 70}")

        # Base limpia antes de cada suite, sea SQLite o MySQL.
        reiniciar_base()

        proceso = subprocess.run(
            [str(PYTHON), str(RAIZ / "pruebas" / archivo)],
            cwd=RAIZ, capture_output=True, text=True,
        )
        salida = "\n".join(
            linea for linea in proceso.stdout.splitlines()
            if "Warning" not in linea and "warnings.warn" not in linea
        )
        print(salida)
        if proceso.returncode != 0 and proceso.stderr:
            print(proceso.stderr[-1500:], file=sys.stderr)

        aprobadas = salida.count("  OK ")
        resultados.append((descripcion, proceso.returncode == 0, aprobadas))

    print(f"\n{'=' * 70}\nRESUMEN\n{'=' * 70}")
    total = 0
    for descripcion, exitosa, aprobadas in resultados:
        total += aprobadas
        marca = "PASA " if exitosa else "FALLA"
        print(f"  [{marca}] {descripcion:<48} {aprobadas:>3} pruebas")

    fallidas = sum(1 for _, exitosa, _ in resultados if not exitosa)
    print(f"\n  {total} verificaciones en {len(SUITES)} suites"
          f" · {len(SUITES) - fallidas} suites correctas, {fallidas} con fallas")

    return 1 if fallidas else 0


if __name__ == "__main__":
    sys.exit(main())
