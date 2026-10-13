"""Ejecuta las suites de prueba del sistema.

    .venv\\Scripts\\python.exe pruebas\\ejecutar_todas.py            todas, salida resumida
    .venv\\Scripts\\python.exe pruebas\\ejecutar_todas.py 09 17      solo esas suites
    .venv\\Scripts\\python.exe pruebas\\ejecutar_todas.py --detalle  salida completa
    .venv\\Scripts\\python.exe pruebas\\ejecutar_todas.py --externas incluye OSRM en vivo

Corren contra MySQL en la base de PRUEBAS_DATABASE_URL o, sin la variable,
mysql+pymysql://sgds:sgds_clave@127.0.0.1:3307/logistica_pruebas. Se niegan a
correr si el nombre de la base no termina en "_pruebas", porque cada suite la
borra y la vuelve a sembrar al arrancar (una sola vez, tambien corrida sola).

Ninguna suite contacta servicios externos: Make y Retell quedan sin
configurar y la red se limita a la propia maquina (ver _preparar.py). Solo con
--externas la suite 03 consulta el servidor publico de OSRM.
"""

import argparse
import os
import pathlib
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _preparar import BASE_PRUEBAS, ENTORNO_PRUEBAS, PYTHON, RAIZ, nombre_base  # noqa: E402

SUITES = [
    ("prueba_01_acceso.py", "RF1 · Autenticacion, roles y tablero"),
    ("prueba_02_pedidos_csv.py", "RF2 · Pedidos manuales, CSV e inventario"),
    ("prueba_03_ruteo.py", "RF3 · Servicio de ruteo y respaldo local"),
    ("prueba_04_rutas_web.py", "RF3 · Planificacion de rutas y mapa"),
    ("prueba_05_entrega_inventario.py", "RF4/RF5 · Entrega en terreno e inventario"),
    ("prueba_06_analitica_rendimiento.py", "RF6/RNF2 · Analitica y tiempos de respuesta"),
    ("prueba_07_clientes_portal.py", "RF1/RF2 · Clientes normalizados y portal"),
    ("prueba_08_administracion.py", "RF1 · Administracion de cuentas y clientes"),
    ("prueba_09_asistente.py", "RF4 · Asistente de voz y avisos por Make"),
    ("prueba_10_automatizaciones.py", "RF4/RF6 · Automatizaciones con Make"),
    ("prueba_11_asistente_roles.py", "RF1/RF4 · Asistente de voz por rol"),
    ("prueba_12_sincronizacion_retell.py", "RF4 · Configuracion y sincronizacion con Retell"),
    ("prueba_13_configuracion.py", "RF4/RF6 · Configuracion del negocio"),
    ("prueba_14_reglas_pantalla.py", "RF1/RF2/RF3 · Reglas de pantalla en servicios"),
    ("prueba_15_auditoria.py", "RF1/RF4 · Auditoria, bitacora y eventos"),
    ("prueba_16_cobertura_voz.py", "RF4 · Cobertura por voz de la pantalla"),
    ("prueba_17_voz_fase3.py", "RF1/RF4 · Voz: maestros, rutas, usuarios y config."),
    ("prueba_18_voz_fase4.py", "RF1/RF4/RF5 · Voz: entregas, analitica y solicitudes"),
]


def argumentos():
    parser = argparse.ArgumentParser(description="Ejecuta las suites de prueba (MySQL).")
    parser.add_argument("suites", nargs="*", help="numeros de suite a correr, p. ej. 09 17")
    parser.add_argument("--detalle", action="store_true", help="muestra la salida completa")
    parser.add_argument("--externas", action="store_true",
                        help="incluye la parte de OSRM en vivo de la suite 03 (usa internet)")
    return parser.parse_args()


def elegidas(numeros):
    if not numeros:
        return SUITES
    pedidas = {n.zfill(2) for n in numeros}
    seleccion = [s for s in SUITES if s[0].split("_")[1] in pedidas]
    if len(seleccion) != len(pedidas):
        sys.exit(f"Suites desconocidas: {sorted(pedidas - {s[0].split('_')[1] for s in seleccion})}")
    return seleccion


def main():
    args = argumentos()
    suites = elegidas(args.suites)
    entorno = dict(ENTORNO_PRUEBAS, PYTHONIOENCODING="utf-8")
    if args.externas:
        entorno["PRUEBAS_EXTERNAS"] = "1"
    else:
        entorno.pop("PRUEBAS_EXTERNAS", None)

    print(f"Base de pruebas: {nombre_base(BASE_PRUEBAS)}"
          + (" · con servicios externos (OSRM)" if args.externas else ""))
    resultados = []
    inicio_total = time.perf_counter()

    # Se siembra una sola vez; cada suite restaura esas filas al arrancar.
    carpeta = tempfile.TemporaryDirectory(prefix="sgds_semilla_")
    entorno["PRUEBAS_SEMILLA"] = os.path.join(carpeta.name, "semilla.pickle")
    siembra = subprocess.run(
        [str(PYTHON), str(RAIZ / "pruebas" / "_preparar.py"), "--guardar-semilla", entorno["PRUEBAS_SEMILLA"]],
        cwd=RAIZ, capture_output=True, text=True, encoding="utf-8", errors="replace", env=entorno,
    )
    if siembra.returncode != 0:
        sys.exit("No se pudo sembrar la base de pruebas:\n" + siembra.stderr.strip()[-2000:])
    print(f"  Semilla preparada en {time.perf_counter() - inicio_total:.1f} s")

    for archivo, descripcion in suites:
        inicio = time.perf_counter()
        proceso = subprocess.run(
            [str(PYTHON), str(RAIZ / "pruebas" / archivo)],
            cwd=RAIZ, capture_output=True, text=True, encoding="utf-8", errors="replace", env=entorno,
        )
        segundos = time.perf_counter() - inicio
        lineas = [linea for linea in proceso.stdout.splitlines()
                  if "Warning" not in linea and "warnings.warn" not in linea]
        aprobadas = sum(1 for linea in lineas if linea.startswith("  OK "))
        fallas = [linea for linea in lineas if linea.startswith("  FALLA")]
        exitosa = proceso.returncode == 0
        resultados.append((descripcion, exitosa, aprobadas, len(fallas), segundos))

        marca = "PASA " if exitosa else "FALLA"
        print(f"  [{marca}] {archivo[7:9]} {descripcion:<50} {aprobadas:>4} OK"
              f"{f'  {len(fallas)} FALLA' if fallas else ''}  {segundos:6.1f} s")
        if args.detalle:
            print("\n".join(lineas))
            if proceso.stderr:
                print(proceso.stderr, file=sys.stderr)
        else:
            for linea in fallas:
                print("        " + linea.strip())
            if not exitosa and not fallas:
                # Se cayo antes de terminar: el final del error basta para ubicarlo.
                print("\n".join("        " + l for l in proceso.stderr.strip().splitlines()[-12:]))

    total = sum(r[2] for r in resultados)
    total_fallas = sum(r[3] for r in resultados)
    fallidas = sum(1 for r in resultados if not r[1])
    print(f"\n  {total} verificaciones en {len(suites)} suites · "
          f"{len(suites) - fallidas} correctas, {fallidas} con fallas"
          f"{f' ({total_fallas} FALLA)' if total_fallas else ''} · "
          f"{time.perf_counter() - inicio_total:.1f} s")
    return 1 if fallidas else 0


if __name__ == "__main__":
    os.chdir(RAIZ)
    sys.exit(main())
