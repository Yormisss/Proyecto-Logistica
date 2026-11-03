#!/usr/bin/env bash
# =============================================================================
# Rutalytics - Paso 1: recursos de Azure (correr en Azure Cloud Shell, Bash)
#
#   bash infra/azure/01_recursos.sh
#
# Crea (o reutiliza si ya existen):
#   - Grupo de recursos rg-rutalytics
#   - Azure Container Registry (Standard, gratis 12 meses en la cuenta gratuita)
#   - Azure Database for MySQL Flexible Server B1ms + base "logistica"
#     (750 h/mes gratis 12 meses; TLS obligatorio)
#   - Entorno de Azure Container Apps y la app "rutalytics" (imagen temporal;
#     el pipeline de Azure DevOps publica la imagen real)
#
# Genera una sola vez las claves (MySQL, SECRET_KEY, CLAVE_CIFRADO y las
# contrasenas de las cuentas demo) y las guarda en ~/rutalytics/estado.env,
# dentro del almacenamiento persistente de Cloud Shell. NUNCA regenerar
# CLAVE_CIFRADO: los datos cifrados con otra clave no se pueden leer.
#
# Variables opcionales: UBICACION (eastus2), GRUPO (rg-rutalytics).
# =============================================================================
set -euo pipefail

UBICACION="${UBICACION:-eastus2}"
GRUPO="${GRUPO:-rg-rutalytics}"
DIR_ESTADO="$HOME/rutalytics"
ESTADO="$DIR_ESTADO/estado.env"
mkdir -p "$DIR_ESTADO"; chmod 700 "$DIR_ESTADO"

paso() { printf '\n\033[1;33m==> %s\033[0m\n' "$*"; }
generar() { python3 -c "import secrets,string;a=string.ascii_letters+string.digits;print(''.join(secrets.choice(a) for _ in range($1)))"; }
# Contrasena de cuenta demo: cumple la politica (mayuscula, minuscula, numero y
# simbolo) y solo usa caracteres seguros en la linea de comandos de Windows.
generar_demo() { echo "$1-$(generar 6)-$(( RANDOM % 90 + 10 ))a"; }

SUSCRIPCION=$(az account show --query id -o tsv)
SUFIJO=$(echo -n "$SUSCRIPCION" | sha1sum | cut -c1-6)

if [[ -f "$ESTADO" ]]; then
    paso "Reutilizando $ESTADO (claves ya generadas)"
    # shellcheck disable=SC1090
    source "$ESTADO"
else
    paso "Generando claves (una sola vez)"
    ACR="acrrutalytics$SUFIJO"
    MYSQL="mysql-rutalytics-$SUFIJO"
    MYSQL_USUARIO="sgdsadmin"
    MYSQL_CLAVE="$(generar 28)"
    SECRET_KEY="$(generar 48)"
    CLAVE_CIFRADO="$(generar 48)"
    SEMILLA_CLAVE_ADMIN="$(generar_demo Admin)"
    SEMILLA_CLAVE_GESTOR="$(generar_demo Gestor)"
    SEMILLA_CLAVE_CONDUCTOR="$(generar_demo Conductor)"
    SEMILLA_CLAVE_CLIENTE="$(generar_demo Cliente)"
    AUTOMATIZACION_TOKEN="$(generar 40)"
    cat > "$ESTADO" <<EOF
UBICACION=$UBICACION
GRUPO=$GRUPO
ACR=$ACR
MYSQL=$MYSQL
MYSQL_USUARIO=$MYSQL_USUARIO
MYSQL_CLAVE=$MYSQL_CLAVE
ENTORNO_CA=cae-rutalytics
APP=rutalytics
JOB=rutalytics-resumen
SECRET_KEY=$SECRET_KEY
CLAVE_CIFRADO=$CLAVE_CIFRADO
SEMILLA_CLAVE_ADMIN=$SEMILLA_CLAVE_ADMIN
SEMILLA_CLAVE_GESTOR=$SEMILLA_CLAVE_GESTOR
SEMILLA_CLAVE_CONDUCTOR=$SEMILLA_CLAVE_CONDUCTOR
SEMILLA_CLAVE_CLIENTE=$SEMILLA_CLAVE_CLIENTE
AUTOMATIZACION_TOKEN=$AUTOMATIZACION_TOKEN
EOF
    chmod 600 "$ESTADO"
    # shellcheck disable=SC1090
    source "$ESTADO"
fi

paso "Registrando proveedores de recursos (la primera vez tarda unos minutos)"
for proveedor in Microsoft.App Microsoft.OperationalInsights Microsoft.DBforMySQL Microsoft.ContainerRegistry; do
    az provider register --namespace "$proveedor" --wait -o none
    echo "  $proveedor: $(az provider show -n "$proveedor" --query registrationState -o tsv)"
done
az extension add --name containerapp --upgrade --only-show-errors -y >/dev/null

paso "Grupo de recursos $GRUPO en $UBICACION"
az group create -n "$GRUPO" -l "$UBICACION" -o none

paso "Container Registry $ACR"
if ! az acr show -n "$ACR" -g "$GRUPO" -o none 2>/dev/null; then
    az acr create -n "$ACR" -g "$GRUPO" -l "$UBICACION" --sku Standard --admin-enabled true -o none
fi
ACR_SERVIDOR=$(az acr show -n "$ACR" --query loginServer -o tsv)
ACR_USUARIO=$(az acr credential show -n "$ACR" --query username -o tsv)
ACR_CLAVE=$(az acr credential show -n "$ACR" --query "passwords[0].value" -o tsv)

paso "MySQL Flexible Server $MYSQL (B1ms; crearlo tarda 5 a 10 minutos)"
if ! az mysql flexible-server show -n "$MYSQL" -g "$GRUPO" -o none 2>/dev/null; then
    az mysql flexible-server create -n "$MYSQL" -g "$GRUPO" -l "$UBICACION" \
        --admin-user "$MYSQL_USUARIO" --admin-password "$MYSQL_CLAVE" \
        --tier Burstable --sku-name Standard_B1ms --storage-size 32 --version 8.0.21 \
        --public-access 0.0.0.0 --backup-retention 7 --yes -o none
fi
if ! az mysql flexible-server db show -g "$GRUPO" -s "$MYSQL" -d logistica -o none 2>/dev/null; then
    az mysql flexible-server db create -g "$GRUPO" -s "$MYSQL" -d logistica \
        --charset utf8mb4 --collation utf8mb4_unicode_ci -o none
fi
MYSQL_HOST=$(az mysql flexible-server show -n "$MYSQL" -g "$GRUPO" --query fullyQualifiedDomainName -o tsv)
DATABASE_URL="mysql+pymysql://$MYSQL_USUARIO:$MYSQL_CLAVE@$MYSQL_HOST:3306/logistica"

paso "Entorno de Container Apps $ENTORNO_CA"
if ! az containerapp env show -n "$ENTORNO_CA" -g "$GRUPO" -o none 2>/dev/null; then
    az containerapp env create -n "$ENTORNO_CA" -g "$GRUPO" -l "$UBICACION" -o none
fi

paso "Container App $APP"
if ! az containerapp show -n "$APP" -g "$GRUPO" -o none 2>/dev/null; then
    # Imagen temporal hasta que el pipeline publique la de Rutalytics.
    az containerapp create -n "$APP" -g "$GRUPO" --environment "$ENTORNO_CA" \
        --image mcr.microsoft.com/k8se/quickstart:latest --target-port 80 --ingress external \
        --min-replicas 1 --max-replicas 1 --cpu 0.5 --memory 1.0Gi \
        --registry-server "$ACR_SERVIDOR" --registry-username "$ACR_USUARIO" --registry-password "$ACR_CLAVE" \
        -o none
fi
FQDN=$(az containerapp show -n "$APP" -g "$GRUPO" --query properties.configuration.ingress.fqdn -o tsv)
URL_PUBLICA="https://$FQDN"

{
    grep -v -E '^(ACR_SERVIDOR|MYSQL_HOST|DATABASE_URL|URL_PUBLICA|SUSCRIPCION)=' "$ESTADO"
    echo "ACR_SERVIDOR=$ACR_SERVIDOR"
    echo "MYSQL_HOST=$MYSQL_HOST"
    echo "DATABASE_URL=$DATABASE_URL"
    echo "URL_PUBLICA=$URL_PUBLICA"
    echo "SUSCRIPCION=$SUSCRIPCION"
} > "$ESTADO.tmp" && mv "$ESTADO.tmp" "$ESTADO" && chmod 600 "$ESTADO"

paso "Listo"
cat <<EOF
  Aplicacion (URL_PUBLICA):  $URL_PUBLICA
  Registry:                  $ACR_SERVIDOR
  MySQL:                     $MYSQL_HOST (base logistica)
  Estado y claves:           $ESTADO   (no lo borre; no lo comparta)

  Cuentas demo en Azure (contrasenas generadas, NO son las del README):
    admin@sgds.com          $SEMILLA_CLAVE_ADMIN
    despachador@sgds.com    $SEMILLA_CLAVE_GESTOR
    conductor1@sgds.com     $SEMILLA_CLAVE_CONDUCTOR
    cliente@sgds.com        $SEMILLA_CLAVE_CLIENTE

  Siguiente: bash infra/azure/02_devops.sh <organizacion-de-azure-devops>
EOF
