#!/bin/sh
# Arranque del contenedor: prepara la base y levanta gunicorn.
#
# 1. `init-db` es idempotente: crea las tablas que falten y la configuracion
#    inicial (motivos de fallo, centro de distribucion, roles del sistema).
# 2. Con SEMBRAR_DEMO=1 carga los datos de demostracion la primera vez; si la
#    base ya tiene usuarios, seed.py no hace nada. En produccion exige las
#    variables SEMILLA_CLAVE_* (las contrasenas del README no sirven en linea).
# 3. gunicorn con 2 workers y 4 hilos: los avisos a Make salen en segundo plano
#    y las funciones de Retell no deben esperar a otra peticion lenta.
set -e

flask --app run init-db

if [ "${SEMBRAR_DEMO:-0}" = "1" ]; then
    python seed.py
fi

exec gunicorn "run:app" \
    --bind "0.0.0.0:${PORT:-8000}" \
    --workers "${GUNICORN_WORKERS:-2}" \
    --threads "${GUNICORN_THREADS:-4}" \
    --timeout 60 \
    --access-logfile - \
    --error-logfile -
