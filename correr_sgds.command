#!/bin/bash
# Levanta el SGDS en http://localhost:5001
cd "$(dirname "$0")" || exit 1
export PATH="/usr/local/bin:/opt/homebrew/bin:/Applications/Docker.app/Contents/Resources/bin:$PATH"
mysql_ok() { nc -z 127.0.0.1 3306 >/dev/null 2>&1; }
if ! mysql_ok && command -v docker >/dev/null 2>&1; then
  echo ">> MySQL no responde; intentando levantarlo con Docker..."
  (docker info >/dev/null 2>&1 || open -a Docker) 
  for i in $(seq 1 30); do docker info >/dev/null 2>&1 && break; sleep 2; done
  docker compose up -d >/dev/null 2>&1
  for i in $(seq 1 30); do mysql_ok && break; sleep 2; done
fi
if mysql_ok; then
  echo ">> Usando MySQL (configuracion de .env)"
else
  echo ">> MySQL no disponible: usando SQLite local (instance/logistica.db)"
  export DATABASE_URL="sqlite:///$(pwd)/instance/logistica.db"
fi
IP_LAN=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)
echo ">> En este Mac:            http://localhost:5001"
echo ">> Desde otro dispositivo: http://${IP_LAN:-<IP-de-tu-Mac>}:5001  (misma red)"
echo ">> Ctrl+C para detener"
.venv/bin/python run.py
