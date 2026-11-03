#!/usr/bin/env bash
# =============================================================================
# Rutalytics - Paso 3: tablero de Azure Boards (correr en Azure Cloud Shell)
#
#   export AZURE_DEVOPS_EXT_PAT=<token personal de Azure DevOps>
#   bash infra/azure/03_backlog.sh <organizacion> [proyecto] [correo-responsable]
#
# Carga el backlog del proyecto (proceso Scrum): los sprints con sus fechas,
# una Feature por requisito (RF1-RF6, RNF1-RNF5 y el despliegue), sus Product
# Backlog Items con criterios de aceptacion, y los Bugs encontrados y corregidos
# en la revision previa al despliegue. Refleja el trabajo hecho en el repositorio
# (historial de git desde el 15/09/2026). Correrlo dos veces duplica los items.
# =============================================================================
set -euo pipefail

ORG="${1:?Uso: bash infra/azure/03_backlog.sh <organizacion> [proyecto] [correo]}"
PROYECTO="${2:-Rutalytics}"
RESPONSABLE="${3:-$(az account show --query user.name -o tsv 2>/dev/null || true)}"
# Cloud Shell entrega las cuentas personales como "live.com#correo"; DevOps espera el correo.
RESPONSABLE="${RESPONSABLE#live.com#}"
EQUIPO="$PROYECTO Team"
paso() { printf '\n\033[1;33m==> %s\033[0m\n' "$*"; }

if [[ -z "${AZURE_DEVOPS_EXT_PAT:-}" ]]; then
    read -r -s -p "Token personal (PAT) de Azure DevOps: " AZURE_DEVOPS_EXT_PAT; echo
    export AZURE_DEVOPS_EXT_PAT
fi
az extension add --name azure-devops --upgrade --only-show-errors -y >/dev/null
az devops configure --defaults organization="https://dev.azure.com/$ORG" project="$PROYECTO"

paso "Sprints"
SPRINTS=(
  "Sprint 1|2026-09-15|2026-09-28"
  "Sprint 2|2026-09-29|2026-10-12"
  "Sprint 3|2026-10-13|2026-10-26"
  "Sprint 4|2026-10-27|2026-11-09"
)
for s in "${SPRINTS[@]}"; do
    IFS='|' read -r nombre inicio fin <<< "$s"
    # El proceso Scrum ya trae "Sprint 1..N": se buscan por nombre y solo se fijan las fechas.
    ruta=$(az boards iteration project list --depth 1 \
        --query "children[?name=='$nombre'].path | [0]" -o tsv 2>/dev/null || true)
    if [[ -z "$ruta" ]]; then
        az boards iteration project create --name "$nombre" --start-date "$inicio" --finish-date "$fin" -o none
        ruta=$(az boards iteration project list --depth 1 --query "children[?name=='$nombre'].path | [0]" -o tsv)
    else
        az boards iteration project update --path "$ruta" --start-date "$inicio" --finish-date "$fin" -o none \
            || echo "    (no se pudieron fijar las fechas de $nombre)"
    fi
    id=$(az boards iteration project list --depth 1 --query "children[?name=='$nombre'].identifier | [0]" -o tsv)
    az boards iteration team add --id "$id" --team "$EQUIPO" -o none 2>/dev/null || true
    echo "  $nombre: $inicio a $fin ($ruta)"
done

# tipo | clave | clave del padre | sprint | estado | titulo | criterios de aceptacion
read -r -d '' BACKLOG <<'DATOS' || true
Feature|RF1||1|Done|RF1 - Gestion de usuarios y roles|Cuatro roles (administrador, gestor, conductor, cliente) con permisos configurables
Product Backlog Item|RF1a|RF1|1|Done|Como administrador quiero crear, editar y desactivar cuentas|Formulario valida correo, rol y documento; las cuentas no se eliminan, se desactivan
Product Backlog Item|RF1b|RF1|3|Done|Como administrador quiero configurar roles y permisos|Cada pantalla y accion de voz respeta el permiso del rol; prueba 25
Product Backlog Item|RF1c|RF1|2|Done|Como cliente quiero un portal para seguir mis pedidos|El cliente solo ve sus pedidos (aislamiento de datos); prueba 07
Feature|RF2||1|Done|RF2 - Ingreso de ordenes de despacho|Pedidos manuales y por CSV con validacion en el servidor
Product Backlog Item|RF2a|RF2|1|Done|Como gestor quiero registrar un pedido manual|Valida cliente, sede, productos, prioridad, ventana y fecha no pasada
Product Backlog Item|RF2b|RF2|1|Done|Como gestor quiero importar pedidos desde un CSV|Vista previa con errores por fila; rechaza archivos que no son CSV; pruebas 02 y 24
Product Backlog Item|RF2c|RF2|2|Done|Como gestor quiero editar o anular un pedido|Solo campos editables segun el estado; anular exige motivo y confirmacion
Feature|RF3||2|Done|RF3 - Generacion de rutas con geolocalizacion|Rutas ordenadas con OSRM y respaldo local sin conexion
Product Backlog Item|RF3a|RF3|2|Done|Como gestor quiero crear una ruta optimizada por distancia o prioridad|Usa OSRM; sin red usa la heuristica local; mapa con la geometria; pruebas 03 y 04
Product Backlog Item|RF3b|RF3|4|Done|Como gestor quiero que la ruta respete conductor, vehiculo y capacidad|Una ruta activa por conductor y vehiculo por dia; capacidad en unidades; prueba 29
Feature|RF4||2|Done|RF4 - Actualizacion de estados en terreno|El conductor actualiza cada parada desde el movil
Product Backlog Item|RF4a|RF4|2|Done|Como conductor quiero iniciar la ruta y marcar entregas o fallos|Maquina de estados; prueba de entrega con geolocalizacion; prueba 05
Product Backlog Item|RF4b|RF4|3|Done|Como gestor quiero que el cliente reciba avisos por correo (Make)|Escenario de Make por evento; un aviso por destinatario; pruebas 10 y 28
Product Backlog Item|RF4c|RF4|4|Done|Como gestor quiero reprogramar un pedido fallido de otro dia|FALLIDO vuelve a PENDIENTE con nueva fecha, en pantalla y por voz; prueba 29
Feature|RF5||2|Done|RF5 - Sincronizacion logica de inventario|El stock solo se descuenta al confirmar la entrega
Product Backlog Item|RF5a|RF5|2|Done|Como gestor quiero que el inventario se descuente solo al entregar|Idempotente y con bloqueo de fila; aviso de stock bajo; prueba 05
Feature|RF6||3|Done|RF6 - Tablero de control con KPIs|KPIs, analitica y resumen diario
Product Backlog Item|RF6a|RF6|3|Done|Como gestor quiero ver los KPIs del dia y la analitica|Entregas a tiempo, tasa de fallo y rendimiento por conductor; prueba 06
Product Backlog Item|RF6b|RF6|4|Done|Como gestor quiero recibir el resumen diario por correo|Boton, voz y tarea programada 18:00 Bogota; prueba 27
Feature|IA||3|Done|Agente de IA - Asistente de voz Gaby (Retell)|Un agente por rol con funciones del servidor y confirmacion
Product Backlog Item|IAa|IA|3|Done|Como usuario quiero operar el sistema por voz segun mi rol|Funciones agrupadas por entidad; firma de Retell verificada; pruebas 09, 11 y 16 a 18
Product Backlog Item|IAb|IA|4|Done|Como administrador quiero que la voz respete permisos y confirme cambios|Confirmacion en el servidor; datos cifrados nunca se leen; prueba 26
Feature|RNF1||1|Done|RNF1 - Interfaz responsiva mobile-first|Vistas del conductor pensadas para el movil
Feature|RNF2||3|Done|RNF2 - Tiempos de respuesta 3-5 s|Cada respuesta se mide en el servidor
Feature|RNF3||1|Done|RNF3 - Arquitectura MVC y despliegue|Modelos, servicios, controladores y vistas separados
Feature|RNF4||2|Done|RNF4 - Integrabilidad SQLite y MySQL|El mismo modelo corre en SQLite y MySQL 8
Feature|RNF5||3|Done|RNF5 - Seguridad de acceso|Bloqueo por intentos, sesion unica, cifrado y recuperacion
Product Backlog Item|RNF5a|RNF5|3|Done|Como administrador quiero bloquear el acceso tras 5 intentos fallidos|Bloqueo de 60 s por correo; prueba 20
Product Backlog Item|RNF5b|RNF5|3|Done|Como usuario quiero recuperar mi contrasena por correo|Enlace de un solo uso valido 30 minutos; prueba 22
Product Backlog Item|RNF5c|RNF5|3|Done|Como responsable de datos quiero cifrar documento y telefono|Fernet con CLAVE_CIFRADO e indice ciego; prueba 23
Feature|DEV||4|In Progress|Despliegue en Azure y DevOps|Aplicacion en linea con CI/CD
Product Backlog Item|DEVa|DEV|4|Done|Como equipo queremos la aplicacion en una imagen Docker|Dockerfile con usuario sin privilegios; scripts con fin de linea LF
Product Backlog Item|DEVb|DEV|4|Committed|Como equipo queremos un pipeline de CI/CD en Azure DevOps|Pruebas, SAST, SCA, imagen y despliegue en cada push a main
Product Backlog Item|DEVc|DEV|4|Committed|Como equipo queremos la aplicacion en linea en Azure|Container Apps + MySQL Flexible con TLS; URL fija para Retell sin ngrok
Bug|B1|DEV|4|Done|Contrasenas demo del README validas en produccion|En produccion la siembra exige SEMILLA_CLAVE_* y no las imprime
Bug|B2|RF4|4|Done|Un pedido fallido de un dia anterior no tenia salida|Se agrego Reprogramar (pantalla y voz)
Bug|B3|RF3|4|Done|Un conductor podia tener dos rutas activas el mismo dia y solo veia una|Validacion al crear la ruta
Bug|B4|RF3|4|Done|La capacidad del vehiculo no se validaba|Validacion por unidades al crear y al agregar paradas
Bug|B5|RF2|4|Done|Se aceptaban pedidos con fecha de despacho pasada|Validacion en pantalla, voz y CSV
Bug|B6|RNF5|4|Done|Dependencias con vulnerabilidades conocidas (urllib3, Werkzeug)|pip-audit sin hallazgos tras actualizar
DATOS

paso "Items del backlog"
declare -A IDS=()
while IFS='|' read -r tipo clave padre sprint estado titulo criterios; do
    [[ -z "$tipo" ]] && continue
    campos=()
    case "$tipo" in
        "Product Backlog Item") campos=(--fields "Microsoft.VSTS.Common.AcceptanceCriteria=$criterios" "Microsoft.VSTS.Scheduling.Effort=3") ;;
        "Bug") campos=(--fields "Microsoft.VSTS.TCM.ReproSteps=$titulo" "Microsoft.VSTS.Common.AcceptanceCriteria=$criterios") ;;
    esac
    asignado=()
    [[ -n "$RESPONSABLE" ]] && asignado=(--assigned-to "$RESPONSABLE")
    id=$(az boards work-item create --type "$tipo" --title "$titulo" --description "$criterios" \
        --iteration "$PROYECTO\\Sprint $sprint" --area "$PROYECTO" "${asignado[@]}" "${campos[@]}" \
        --query id -o tsv)
    IDS["$clave"]="$id"
    az boards work-item update --id "$id" --state "$estado" -o none 2>/dev/null \
        || echo "    (no se pudo pasar #$id a $estado; ajustelo en el tablero)"
    if [[ -n "$padre" && -n "${IDS[$padre]:-}" ]]; then
        az boards work-item relation add --id "$id" --relation-type parent --target-id "${IDS[$padre]}" -o none
    fi
    echo "  #$id [$tipo] $titulo ($estado, Sprint $sprint)"
done <<< "$BACKLOG"

paso "Listo: https://dev.azure.com/$ORG/$PROYECTO/_backlogs"
