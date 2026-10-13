# Envia a Make avisos de ejemplo con los mismos campos que arma la app SGDS.
# Sirve para que Make aprenda la estructura de datos y para probar los escenarios
# sin depender de Retell. Lee las URLs y el correo del .env (aunque la linea este
# comentada con #). Todos los correos de prueba van a CORREO_OPERACIONES.
#
# Uso, desde C:\Proyecto-Logistica:
#   powershell -ExecutionPolicy Bypass -File .\ejemplos\make\enviar_ejemplos_make.ps1 -Modo estructura
#   powershell -ExecutionPolicy Bypass -File .\ejemplos\make\enviar_ejemplos_make.ps1 -Modo avisos
#   powershell -ExecutionPolicy Bypass -File .\ejemplos\make\enviar_ejemplos_make.ps1 -Modo credencial
#
#   estructura : un solo aviso (tipo "estructura") con TODOS los campos, para
#                "Redetermine data structure". Ninguna ruta del Router lo procesa.
#   avisos     : pedido_estado, stock_bajo, solicitud_contacto y accion_asistente.
#   credencial : credencial_usuario, al webhook aparte de credenciales.
param(
    [ValidateSet('estructura', 'avisos', 'credencial')]
    [string]$Modo = 'avisos',
    [string]$Archivo = '.env'
)
$ErrorActionPreference = 'Stop'

function Leer-Env([string]$clave) {
    $comentada = $null
    foreach ($linea in Get-Content -Path $Archivo -Encoding UTF8) {
        if ($linea -match "^\s*(#\s*)?$clave\s*=\s*(.*)$") {
            $valor = $Matches[2].Trim().Trim('"').Trim("'")
            if (-not $Matches[1]) { return $valor }
            if (-not $comentada) { $comentada = $valor }
        }
    }
    return $comentada
}

$urlAvisos  = Leer-Env 'MAKE_WEBHOOK_URL'
$urlCred    = Leer-Env 'MAKE_WEBHOOK_CREDENCIALES_URL'
$apiKey     = Leer-Env 'MAKE_WEBHOOK_KEY'
$correo     = Leer-Env 'CORREO_OPERACIONES'
$urlPublica = Leer-Env 'URL_PUBLICA'
if (-not $correo) { throw 'Falta CORREO_OPERACIONES en el .env: ahi llegan los correos de prueba.' }

$ahora     = Get-Date
$hora      = $ahora.ToString('yyyy-MM-dd HH:mm')
$fechaHora = $ahora.ToString('yyyy-MM-ddTHH:mm:ss') + '-05:00'
$codigo    = 'PED-' + $ahora.ToString('yyyyMMdd') + '-001'

function Con-Actor($datos, [string]$nombre, [string]$rol, [string]$origen) {
    $datos['hora'] = $hora
    $datos['fecha_hora'] = $fechaHora
    $datos['realizado_por'] = $nombre
    $datos['rol_realizado_por'] = $rol
    $datos['origen'] = $origen
    $datos['realizado_por_publico'] = $rol
    return $datos
}

function Enviar([string]$destino, $datos) {
    if (-not $destino) { throw "No hay URL de webhook para el aviso '$($datos['tipo'])'. Revise el .env." }
    $json  = $datos | ConvertTo-Json -Depth 5 -Compress
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
    $cabeceras = @{}
    if ($apiKey) { $cabeceras['x-make-apikey'] = $apiKey }
    Invoke-RestMethod -Method Post -Uri $destino -Headers $cabeceras `
        -ContentType 'application/json; charset=utf-8' -Body $bytes | Out-Null
    Write-Host "Enviado: $($datos['tipo'])"
}

$detalle = "funcion: admin/configuracion:renombrar-motivo`ndatos / motivo: Cliente ausente`ndatos / nuevo nombre: Cliente no se encontraba`nrespuesta: Listo, renombre el motivo."

switch ($Modo) {
    'estructura' {
        $d = [ordered]@{
            tipo = 'estructura'
            codigo = $codigo; estado = 'FALLIDO'; cliente = 'Supermercado El Portal'
            correo = $correo; direccion = 'Av. Cra 68 #75-50, Bogota'; ventana = '08:00 - 11:00'
            motivo = 'Cliente ausente'
            sku = 'SKU-1001'; producto = 'Caja bebidas 12 und'; stock_actual = 60; stock_minimo = 60
            negativo = $false; pedido = $codigo
            telefono = '3115550111'; correo_destino = $correo
            id_auditoria = 1; usuario = 'Laura Gomez'; rol = 'Administrador'
            accion = 'Renombrar motivo de fallo'; entidad = 'configuracion'
            resumen = 'Voy a renombrar el motivo Cliente ausente a Cliente no se encontraba.'
            detalle = $detalle; resultado = 'exito'
        }
        Enviar $urlAvisos (Con-Actor $d 'Laura Gomez' 'Administrador' 'voz')
    }
    'avisos' {
        $d = [ordered]@{ tipo = 'pedido_estado'; codigo = $codigo; estado = 'FALLIDO'
            cliente = 'Supermercado El Portal'; correo = $correo
            direccion = 'Av. Cra 68 #75-50, Bogota'; ventana = '08:00 - 11:00'; motivo = 'Cliente ausente' }
        Enviar $urlAvisos (Con-Actor $d 'Andres Molina' 'Conductor' 'voz')

        $d = [ordered]@{ tipo = 'stock_bajo'; sku = 'SKU-1001'; producto = 'Caja bebidas 12 und'
            stock_actual = 60; stock_minimo = 60; negativo = $false; pedido = $null; correo_destino = $correo }
        Enviar $urlAvisos (Con-Actor $d 'Carlos Rueda' 'Gestor logístico' 'pantalla')

        $d = [ordered]@{ tipo = 'solicitud_contacto'; cliente = 'Supermercado El Portal'; correo = $correo
            telefono = '3115550111'; motivo = 'Cambiar la ventana del pedido de hoy'; correo_destino = $correo }
        Enviar $urlAvisos (Con-Actor $d 'Marcela Rios' 'Cliente' 'voz')

        $d = [ordered]@{ tipo = 'accion_asistente'; id_auditoria = 1; usuario = 'Laura Gomez'; rol = 'Administrador'
            accion = 'Renombrar motivo de fallo'; entidad = 'configuracion'
            resumen = 'Voy a renombrar el motivo Cliente ausente a Cliente no se encontraba.'
            detalle = $detalle; resultado = 'exito'; correo_destino = $correo }
        Enviar $urlAvisos (Con-Actor $d 'Laura Gomez' 'Administrador' 'voz')
    }
    'credencial' {
        $d = [ordered]@{ tipo = 'credencial_usuario'; nombre = 'Usuario de Prueba'; correo = $correo
            contrasena = 'EjemploNoUsar-123'; url_acceso = $urlPublica
            creado_por = 'Laura Gomez (Administrador)'; accion = 'creación'; correo_destino = $correo }
        Enviar $urlCred (Con-Actor $d 'Laura Gomez' 'Administrador' 'voz')
    }
}
