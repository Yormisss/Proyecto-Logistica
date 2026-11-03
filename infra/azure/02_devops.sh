#!/usr/bin/env bash
# =============================================================================
# Rutalytics - Paso 2: Azure DevOps (correr en Azure Cloud Shell, Bash)
#
#   export AZURE_DEVOPS_EXT_PAT=<token personal de Azure DevOps>
#   bash infra/azure/02_devops.sh <organizacion> [proyecto]
#
# Requiere haber corrido 01_recursos.sh (lee ~/rutalytics/estado.env).
# Opcional: subir el .env del proyecto a ~/rutalytics/.env (boton "Cargar"
# de Cloud Shell) para pasar las claves de Retell y Make al grupo de variables.
#
# Crea o reutiliza:
#   - El proyecto (proceso Scrum) si no existe
#   - La conexion de servicio "azure-rutalytics" (entidad de servicio con rol
#     Contributor solo sobre el grupo de recursos)
#   - El grupo de variables "rutalytics-produccion" (secretos marcados como
#     secretos: no se ven en la interfaz ni en los registros del pipeline)
#   - El pipeline "Rutalytics CI-CD" sobre azure-pipelines.yml del repositorio
# =============================================================================
set -euo pipefail

ORG="${1:?Uso: bash infra/azure/02_devops.sh <organizacion> [proyecto]}"
PROYECTO="${2:-Rutalytics}"
ORG_URL="https://dev.azure.com/$ORG"
DIR_ESTADO="$HOME/rutalytics"
ESTADO="$DIR_ESTADO/estado.env"
ENV_PROYECTO="$DIR_ESTADO/.env"
GRUPO_VARIABLES="rutalytics-produccion"
CONEXION="azure-rutalytics"

paso() { printf '\n\033[1;33m==> %s\033[0m\n' "$*"; }
[[ -f "$ESTADO" ]] || { echo "Falta $ESTADO: corra primero infra/azure/01_recursos.sh"; exit 1; }
# shellcheck disable=SC1090
source "$ESTADO"

if [[ -z "${AZURE_DEVOPS_EXT_PAT:-}" ]]; then
    read -r -s -p "Token personal (PAT) de Azure DevOps: " AZURE_DEVOPS_EXT_PAT; echo
    export AZURE_DEVOPS_EXT_PAT
fi

az extension add --name azure-devops --upgrade --only-show-errors -y >/dev/null
az devops configure --defaults organization="$ORG_URL" project="$PROYECTO"

paso "Proyecto $PROYECTO"
if ! az devops project show --project "$PROYECTO" -o none 2>/dev/null; then
    az devops project create --name "$PROYECTO" --process Scrum --visibility private \
        --description "Rutalytics (SGDS): gestion de despachos de ultima milla" -o none
fi
echo "  $ORG_URL/$PROYECTO"

paso "Repositorio"
RAMA=$(az repos show --repository "$PROYECTO" --query defaultBranch -o tsv 2>/dev/null || true)
if [[ -z "$RAMA" ]]; then
    echo "  El repositorio $PROYECTO esta vacio. Desde el PC (CMD, en C:\\Proyecto-Logistica):"
    echo "    git remote add azure $ORG_URL/$PROYECTO/_git/$PROYECTO"
    echo "    git push azure main"
    echo "  y vuelva a correr este script para crear el pipeline."
else
    echo "  Rama por defecto: $RAMA"
fi

paso "Conexion de servicio $CONEXION"
ID_CONEXION=$(az devops service-endpoint list --query "[?name=='$CONEXION'].id | [0]" -o tsv)
if [[ -z "$ID_CONEXION" ]]; then
    NOMBRE_SUSCRIPCION=$(az account show --query name -o tsv)
    INQUILINO=$(az account show --query tenantId -o tsv)
    SP=$(az ad sp create-for-rbac --name "sp-rutalytics-devops" --role Contributor \
        --scopes "/subscriptions/$SUSCRIPCION/resourceGroups/$GRUPO" -o json)
    SP_ID=$(echo "$SP" | python3 -c "import json,sys;print(json.load(sys.stdin)['appId'])")
    AZURE_DEVOPS_EXT_AZURE_RM_SERVICE_PRINCIPAL_KEY=$(echo "$SP" | python3 -c "import json,sys;print(json.load(sys.stdin)['password'])")
    export AZURE_DEVOPS_EXT_AZURE_RM_SERVICE_PRINCIPAL_KEY
    echo "  Esperando a que la entidad de servicio se propague..."
    sleep 30
    ID_CONEXION=$(az devops service-endpoint azurerm create --name "$CONEXION" \
        --azure-rm-service-principal-id "$SP_ID" --azure-rm-subscription-id "$SUSCRIPCION" \
        --azure-rm-subscription-name "$NOMBRE_SUSCRIPCION" --azure-rm-tenant-id "$INQUILINO" \
        --query id -o tsv)
    unset AZURE_DEVOPS_EXT_AZURE_RM_SERVICE_PRINCIPAL_KEY
fi
az devops service-endpoint update --id "$ID_CONEXION" --enable-for-all true -o none
echo "  Lista ($ID_CONEXION)"

paso "Grupo de variables $GRUPO_VARIABLES"
# Claves de Retell y Make: del .env del proyecto subido a Cloud Shell, si esta.
declare -A ENV_LOCAL=()
if [[ -f "$ENV_PROYECTO" ]]; then
    while IFS='=' read -r clave valor; do
        ENV_LOCAL["$clave"]="$valor"
    done < <(python3 - "$ENV_PROYECTO" <<'PY'
import sys
for linea in open(sys.argv[1], encoding="utf-8-sig"):
    linea = linea.strip()
    if not linea or linea.startswith("#") or "=" not in linea:
        continue
    clave, valor = linea.split("=", 1)
    valor = valor.strip().strip('"').strip("'")
    print(f"{clave.strip()}={valor}")
PY
)
    echo "  Leidas ${#ENV_LOCAL[@]} variables de $ENV_PROYECTO"
else
    echo "  (Sin $ENV_PROYECTO: las claves de Retell y Make se pueden agregar despues en Library)"
fi
local_o() { echo "${ENV_LOCAL[$1]:-}"; }

ID_GRUPO=$(az pipelines variable-group list --query "[?name=='$GRUPO_VARIABLES'].id | [0]" -o tsv)
if [[ -z "$ID_GRUPO" ]]; then
    ID_GRUPO=$(az pipelines variable-group create --name "$GRUPO_VARIABLES" --authorize true \
        --description "Configuracion y secretos de produccion de Rutalytics (Azure Container Apps)" \
        --variables GRUPO="$GRUPO" --query id -o tsv)
fi

fijar() {  # fijar NOMBRE VALOR [secreto]
    local nombre="$1" valor="$2" secreto="${3:-false}"
    [[ -z "$valor" ]] && { echo "  - $nombre (vacio, se omite)"; return; }
    if ! az pipelines variable-group variable create --group-id "$ID_GRUPO" --name "$nombre" \
            --value "$valor" --secret "$secreto" -o none 2>/dev/null; then
        az pipelines variable-group variable update --group-id "$ID_GRUPO" --name "$nombre" \
            --value "$valor" --secret "$secreto" -o none
    fi
    if [[ "$secreto" == "true" ]]; then echo "  - $nombre (secreto)"; else echo "  - $nombre = $valor"; fi
}

for v in GRUPO APP JOB ACR ACR_SERVIDOR ENTORNO_CA URL_PUBLICA; do fijar "$v" "${!v}"; done
fijar CONEXION_AZURE "$CONEXION"
# Azure Pipelines reserva el prefijo "secret" en los nombres de variables: la
# SECRET_KEY de Flask se guarda como FLASK_SECRET_KEY (desplegar.ps1 la traduce).
fijar FLASK_SECRET_KEY "$SECRET_KEY" true
for v in DATABASE_URL CLAVE_CIFRADO SEMILLA_CLAVE_ADMIN SEMILLA_CLAVE_GESTOR \
         SEMILLA_CLAVE_CONDUCTOR SEMILLA_CLAVE_CLIENTE AUTOMATIZACION_TOKEN; do
    fijar "$v" "${!v}" true
done
for v in RETELL_API_KEY MAKE_WEBHOOK_URL MAKE_WEBHOOK_KEY MAKE_WEBHOOK_RESUMEN_URL \
         MAKE_WEBHOOK_CREDENCIALES_URL MAKE_WEBHOOK_RESTABLECIMIENTO_URL; do
    fijar "$v" "$(local_o "$v")" true
done
for v in RETELL_AGENTE_CONDUCTOR_ID RETELL_AGENTE_GESTOR_ID RETELL_AGENTE_ADMIN_ID RETELL_AGENTE_CLIENTE_ID \
         RETELL_VOZ_ID CORREO_OPERACIONES SEMILLA_CORREO_ADMIN SEMILLA_CORREO_GESTOR \
         SEMILLA_CORREO_CONDUCTOR SEMILLA_CORREO_CLIENTE URL_OSRM; do
    fijar "$v" "$(local_o "$v")"
done

paso "Pipeline"
if [[ -n "$RAMA" ]]; then
    if ! az pipelines show --name "Rutalytics CI-CD" -o none 2>/dev/null; then
        az pipelines create --name "Rutalytics CI-CD" --repository "$PROYECTO" --repository-type tfsgit \
            --branch main --yml-path azure-pipelines.yml --skip-first-run true -o none
    fi
    echo "  $ORG_URL/$PROYECTO/_build"
fi

paso "Listo"
cat <<EOF
  Falta, en el PC: instalar el agente en el grupo "Default" (Project settings > Agent pools >
  Default > New agent) y dejarlo corriendo con run.cmd. Luego Pipelines > Rutalytics CI-CD >
  Run pipeline. La primera vez, autorice ("Permit") el grupo de variables, la conexion y el entorno.
EOF
