#!/bin/bash
# Instancia de PRUEBAS del SGDS: SQLite aparte + datos de demostracion.
# No toca tu MySQL ni tu .env. Puerto 5050.
cd "$(dirname "$0")" || exit 1
source .venv/bin/activate || { echo "No se encontro .venv"; exit 1; }
export ENTORNO=desarrollo
export DATABASE_URL="sqlite:///$(pwd)/instance/pruebas_sgds.db"
export SECRET_KEY="clave-demo-pruebas"
export FLASK_APP=run
rm -f instance/pruebas_sgds.db
mkdir -p instance
echo ">> Creando esquema..."
flask --app run init-db
echo ">> Cargando datos de demostracion..."
flask --app run sembrar
echo ">> Servidor en http://localhost:5050  (Ctrl+C para detener)"
flask --app run run --port 5050 --no-reload
