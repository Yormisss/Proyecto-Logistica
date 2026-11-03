#!/bin/sh
# Tarea programada (Azure Container Apps Job, todos los dias 23:00 UTC = 18:00
# de Bogota): envia el resumen del dia a Make. Misma imagen que la web.
set -e
exec flask --app run enviar-resumen
