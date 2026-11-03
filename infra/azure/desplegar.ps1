# =============================================================================
# Rutalytics - Despliegue en Azure Container Apps (lo ejecuta el pipeline)
#
# Corre dentro de la tarea AzureCLI@2 (ya autenticada con la conexion de
# servicio). Toma la configuracion y los secretos del grupo de variables
# "rutalytics-produccion", que el pipeline pasa como variables de entorno:
#   1. Guarda los secretos en la Container App (nunca como texto plano).
#   2. Publica la imagen recien construida y apunta las variables a los secretos.
#   3. Crea o actualiza la tarea programada del resumen diario (23:00 UTC).
#   4. Espera a que /salud responda en la URL publica.
# =============================================================================
param(
    [Parameter(Mandatory = $true)][string]$Imagen
)
# "Continue": en Windows PowerShell 5.1 cualquier aviso de az en stderr se volveria un
# error con "Stop". Los fallos se detectan por el codigo de salida (Invocar-Az).
$ErrorActionPreference = "Continue"

# Azure Pipelines no admite variables cuyo nombre empiece por "secret" (prefijo
# reservado, igual que endpoint, input, path y securefile): SECRET_KEY se guarda
# en el grupo como FLASK_SECRET_KEY y aqui se lee con ese nombre.
$ORIGEN = @{ SECRET_KEY = "FLASK_SECRET_KEY" }

function Valor([string]$nombre) {
    if ($ORIGEN.ContainsKey($nombre)) { $nombre = $ORIGEN[$nombre] }
    $v = [Environment]::GetEnvironmentVariable($nombre)
    # Una variable secreta que no existe en el grupo llega como "$(NOMBRE)".
    if ([string]::IsNullOrWhiteSpace($v) -or $v.StartsWith('$(')) { return $null }
    return $v.Trim()
}
# No se llama "Az": PowerShell no distingue mayusculas y la funcion taparia al comando az.
function Invocar-Az {
    & az @args
    # Solo el comando, no los argumentos: llevan secretos y la clave del registro.
    if ($LASTEXITCODE -ne 0) { throw "Fallo: az $($args[0]) $($args[1]) (codigo $LASTEXITCODE)" }
}

# PowerShell 5.1 no siempre negocia TLS 1.2, que exige Azure Container Apps.
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# La tarea AzureCLI@2 usa una configuracion temporal de az (AZURE_CONFIG_DIR):
# las extensiones instaladas en el PC no se ven, y sin terminal az no puede
# preguntar si instala "containerapp". Se instala aqui, sin preguntar.
Write-Host "==> Extension containerapp de Azure CLI"
& az config set extension.use_dynamic_install=yes_without_prompt --only-show-errors 2>$null | Out-Null
Invocar-Az extension add --name containerapp --upgrade --only-show-errors --yes

$grupo = Valor "GRUPO"; $app = Valor "APP"; $job = Valor "JOB"; $entorno = Valor "ENTORNO_CA"
$acr = Valor "ACR"
foreach ($n in @("GRUPO", "APP", "JOB", "ENTORNO_CA", "ACR")) {
    if (-not (Valor $n)) { throw "Falta la variable $n en el grupo rutalytics-produccion" }
}

# Variable de entorno -> nombre del secreto en la Container App.
$secretos = [ordered]@{
    DATABASE_URL                      = "database-url"
    SECRET_KEY                        = "secret-key"
    CLAVE_CIFRADO                     = "clave-cifrado"
    SEMILLA_CLAVE_ADMIN               = "semilla-clave-admin"
    SEMILLA_CLAVE_GESTOR              = "semilla-clave-gestor"
    SEMILLA_CLAVE_CONDUCTOR           = "semilla-clave-conductor"
    SEMILLA_CLAVE_CLIENTE             = "semilla-clave-cliente"
    AUTOMATIZACION_TOKEN              = "automatizacion-token"
    RETELL_API_KEY                    = "retell-api-key"
    MAKE_WEBHOOK_URL                  = "make-webhook-url"
    MAKE_WEBHOOK_KEY                  = "make-webhook-key"
    MAKE_WEBHOOK_RESUMEN_URL          = "make-webhook-resumen-url"
    MAKE_WEBHOOK_CREDENCIALES_URL     = "make-webhook-credenciales-url"
    MAKE_WEBHOOK_RESTABLECIMIENTO_URL = "make-webhook-restablecimiento-url"
}
$planas = @("RETELL_AGENTE_CONDUCTOR_ID", "RETELL_AGENTE_GESTOR_ID", "RETELL_AGENTE_ADMIN_ID",
            "RETELL_AGENTE_CLIENTE_ID", "RETELL_VOZ_ID", "CORREO_OPERACIONES", "URL_OSRM",
            "SEMILLA_CORREO_ADMIN", "SEMILLA_CORREO_GESTOR", "SEMILLA_CORREO_CONDUCTOR",
            "SEMILLA_CORREO_CLIENTE")

foreach ($obligatoria in @("DATABASE_URL", "SECRET_KEY", "CLAVE_CIFRADO")) {
    if (-not (Valor $obligatoria)) { throw "Falta el secreto $obligatoria en el grupo de variables" }
}

$paresSecretos = @(); $envVars = @("ENTORNO=produccion", "SEMBRAR_DEMO=1")
foreach ($nombre in $secretos.Keys) {
    $valor = Valor $nombre
    if ($valor) {
        $paresSecretos += "$($secretos[$nombre])=$valor"
        $envVars += "$nombre=secretref:$($secretos[$nombre])"
    }
}
foreach ($nombre in $planas) {
    $valor = Valor $nombre
    if ($valor) { $envVars += "$nombre=$valor" }
}

Write-Host "==> URL publica"
$fqdn = (Invocar-Az containerapp show -n $app -g $grupo --query properties.configuration.ingress.fqdn -o tsv)
$urlPublica = "https://$fqdn"
$envVars += "URL_PUBLICA=$urlPublica"
Write-Host "    $urlPublica"

Write-Host "==> Secretos de la Container App ($($paresSecretos.Count))"
Invocar-Az containerapp secret set -n $app -g $grupo --secrets @paresSecretos -o none

Write-Host "==> Credenciales del registro y puerto"
$servidor = (Invocar-Az acr show -n $acr --query loginServer -o tsv)
$usuario = (Invocar-Az acr credential show -n $acr --query username -o tsv)
$clave = (Invocar-Az acr credential show -n $acr --query "passwords[0].value" -o tsv)
Invocar-Az containerapp registry set -n $app -g $grupo --server $servidor --username $usuario --password $clave -o none
Invocar-Az containerapp ingress update -n $app -g $grupo --target-port 8000 -o none

Write-Host "==> Imagen $Imagen"
Invocar-Az containerapp update -n $app -g $grupo --image $Imagen --cpu 0.5 --memory 1.0Gi `
    --min-replicas 1 --max-replicas 1 --set-env-vars @envVars -o none

Write-Host "==> Tarea programada $job (resumen diario, 23:00 UTC = 18:00 Bogota)"
$secretosJob = @(); $envJob = @("ENTORNO=produccion")
foreach ($nombre in @("DATABASE_URL", "SECRET_KEY", "CLAVE_CIFRADO", "MAKE_WEBHOOK_RESUMEN_URL", "MAKE_WEBHOOK_KEY")) {
    $valor = Valor $nombre
    if ($valor) {
        $secretosJob += "$($secretos[$nombre])=$valor"
        $envJob += "$nombre=secretref:$($secretos[$nombre])"
    }
}
if (Valor "CORREO_OPERACIONES") { $envJob += "CORREO_OPERACIONES=$(Valor 'CORREO_OPERACIONES')" }
$existe = $false
& az containerapp job show -n $job -g $grupo -o none 2>$null
if ($LASTEXITCODE -eq 0) { $existe = $true }
if (-not $existe) {
    Invocar-Az containerapp job create -n $job -g $grupo --environment $entorno `
        --trigger-type Schedule --cron-expression "0 23 * * *" `
        --replica-timeout 600 --replica-retry-limit 1 --parallelism 1 --replica-completion-count 1 `
        --image $Imagen --cpu 0.25 --memory 0.5Gi --command "/app/docker/resumen.sh" `
        --registry-server $servidor --registry-username $usuario --registry-password $clave `
        --secrets @secretosJob --env-vars @envJob -o none
} else {
    Invocar-Az containerapp job secret set -n $job -g $grupo --secrets @secretosJob -o none
    Invocar-Az containerapp job update -n $job -g $grupo --image $Imagen --set-env-vars @envJob -o none
}

Write-Host "==> Verificando $urlPublica/salud"
$ok = $false
for ($i = 1; $i -le 30; $i++) {
    try {
        $r = Invoke-WebRequest -Uri "$urlPublica/salud" -UseBasicParsing -TimeoutSec 10
        if ($r.StatusCode -eq 200) { $ok = $true; break }
    } catch { }
    Start-Sleep -Seconds 10
}
if (-not $ok) { throw "La aplicacion no respondio en $urlPublica/salud" }
Write-Host "    OK: $urlPublica"
Write-Host "##vso[task.setvariable variable=URL_DESPLEGADA;isOutput=true]$urlPublica"
