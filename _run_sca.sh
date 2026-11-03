#!/bin/bash
# SCA - Analisis de dependencias con pip-audit (usa internet de tu Mac).
cd "$(dirname "$0")" || exit 1
source .venv/bin/activate 2>/dev/null || true
echo ">> Instalando pip-audit (si falta)..."
pip install -q pip-audit 2>/dev/null
echo ">> Ejecutando pip-audit sobre requirements.txt..."
{
  echo "SCA - pip-audit  |  $(date '+%Y-%m-%d %H:%M')"
  echo "Proyecto: SGDS - Sistema de Gestion de Despachos"
  echo "============================================================"
  pip-audit -r requirements.txt --progress-spinner off 2>&1
  echo "============================================================"
  echo "(codigo de salida pip-audit: $?)"
} | tee sca_resultado.txt
echo ">> Listo. Resultado guardado en sca_resultado.txt"
