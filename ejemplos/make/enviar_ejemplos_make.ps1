# Envia a Make avisos de ejemplo con los mismos campos que arma la app Rutalytics.
# Sirve para que Make aprenda la estructura de datos y para probar los escenarios
# sin depender de Retell. Lee las URLs y el correo del .env (aunque la linea este
# comentada con #). Todos los correos de prueba van a CORREO_OPERACIONES.
#
# Uso, desde C:\Proyecto-Logistica:
#   powershell -ExecutionPolicy Bypass -File .\ejemplos\make\enviar_ejemplos_make.ps1 -Modo estructura
#   powershell -ExecutionPolicy Bypass -File .\ejemplos\make\enviar_ejemplos_make.ps1 -Modo avisos
#   powershell -ExecutionPolicy Bypass -File .\ejemplos\make\enviar_ejemplos_make.ps1 -Modo estructura-privado
#   powershell -ExecutionPolicy Bypass -File .\ejemplos\make\enviar_ejemplos_make.ps1 -Modo resumen
#   powershell -ExecutionPolicy Bypass -File .\ejemplos\make\enviar_ejemplos_make.ps1 -Modo credencial
#   powershell -ExecutionPolicy Bypass -File .\ejemplos\make\enviar_ejemplos_make.ps1 -Modo restablecimiento
#
# Escenario "SGDS" (MAKE_WEBHOOK_URL):
#   estructura         : un solo aviso (tipo "estructura") con TODOS los campos de
#                        los avisos de la operacion, para "Redetermine data
#                        structure". Ninguna ruta del Router lo procesa.
#   avisos             : pedido_estado, stock_bajo, solicitud_contacto y accion_asistente.
# Escenario "Rutalytics Privado" (MAKE_WEBHOOK_RESUMEN_URL, MAKE_WEBHOOK_CREDENCIALES_URL
# y MAKE_WEBHOOK_RESTABLECIMIENTO_URL, las tres con la URL de su webhook):
#   estructura-privado : un solo aviso (tipo "estructura") con TODOS los campos de
#                        resumen_diario, credencial_usuario y restablecimiento_contrasena,
#                        con kpis anidado y un elemento en cada lista, a
#                        MAKE_WEBHOOK_RESUMEN_URL.
#   resumen            : un resumen_diario de ejemplo, a MAKE_WEBHOOK_RESUMEN_URL.
#   credencial         : credencial_usuario, a MAKE_WEBHOOK_CREDENCIALES_URL.
#   restablecimiento   : restablecimiento_contrasena, a MAKE_WEBHOOK_RESTABLECIMIENTO_URL.
param(
    [ValidateSet('estructura', 'avisos', 'estructura-privado', 'resumen', 'credencial', 'restablecimiento')]
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
$urlRestab  = Leer-Env 'MAKE_WEBHOOK_RESTABLECIMIENTO_URL'
$urlResumen = Leer-Env 'MAKE_WEBHOOK_RESUMEN_URL'
$apiKey     = Leer-Env 'MAKE_WEBHOOK_KEY'
$correo     = Leer-Env 'CORREO_OPERACIONES'
$urlPublica = Leer-Env 'URL_PUBLICA'
if (-not $correo) { throw 'Falta CORREO_OPERACIONES en el .env: ahi llegan los correos de prueba.' }
if (-not $urlPublica) { $urlPublica = 'https://rutalytics.ejemplo.com' }

$ahora     = Get-Date
$hora      = $ahora.ToString('yyyy-MM-dd HH:mm')
$fecha     = $ahora.ToString('yyyy-MM-dd')
$fechaHora = $ahora.ToString('yyyy-MM-ddTHH:mm:ss') + '-05:00'
$codigo    = 'PED-' + $ahora.ToString('yyyyMMdd') + '-001'

$sistema = 'Sistema SGDS (automático)'
$rolSistema = 'Sistema'

function Con-Actor($datos, [string]$nombre, [string]$rol, [string]$origen) {
    $datos['hora'] = $hora
    $datos['fecha_hora'] = $fechaHora
    $datos['realizado_por'] = $nombre
    $datos['rol_realizado_por'] = $rol
    $datos['origen'] = $origen
    $datos['realizado_por_publico'] = $rol
    return $datos
}

function Enviar([string]$destino, $datos, [string]$variable) {
    if (-not $destino) { throw "Falta $variable en el .env para el aviso '$($datos['tipo'])'." }
    $json  = $datos | ConvertTo-Json -Depth 6 -Compress
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
    $cabeceras = @{}
    if ($apiKey) { $cabeceras['x-make-apikey'] = $apiKey }
    Invoke-RestMethod -Method Post -Uri $destino -Headers $cabeceras `
        -ContentType 'application/json; charset=utf-8' -Body $bytes | Out-Null
    Write-Host "Enviado: $($datos['tipo']) ($variable)"
}

$detalle = "funcion: admin/configuracion:renombrar-motivo`ndatos / motivo: Cliente ausente`ndatos / nuevo nombre: Cliente no se encontraba`nrespuesta: Listo, renombre el motivo."

# Partes de resumen_diario, iguales a las de la app (app/services/resumen_diario.py).
# Sin "hora": el resumen trae solo fecha y fecha_hora, como el GET de respaldo.
function Kpis {
    return [ordered]@{ total = 12; entregados = 9; fallidos = 2; cancelados = 0; pendientes = 1
        tasa_exito = 75.0; cumplimiento_ventana = 88.9 }
}
function Producto-Bajo {
    return [ordered]@{ sku = 'SKU-1001'; producto = 'Caja bebidas 12 und'; stock_actual = 40
        stock_minimo = 60; negativo = $false }
}
function Fallido([string]$nombre, [string]$rol, [string]$origen) {
    return [ordered]@{ codigo = $codigo; cliente = 'Supermercado El Portal'; correo = $correo
        direccion = 'Av. Cra 68 #75-50, Bogota'; motivo = 'Cliente ausente'
        realizado_por = $nombre; rol_realizado_por = $rol; origen = $origen; realizado_por_publico = $rol }
}
$resumenHtml = "<h2>Resumen del $fecha</h2><p>12 pedidos: 9 entregados, 2 fallidos, 1 pendiente. Tasa de exito: 75 %.</p>"

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
        Enviar $urlAvisos (Con-Actor $d 'Laura Gomez' 'Administrador' 'voz') 'MAKE_WEBHOOK_URL'
    }
    'avisos' {
        $d = [ordered]@{ tipo = 'pedido_estado'; codigo = $codigo; estado = 'FALLIDO'
            cliente = 'Supermercado El Portal'; correo = $correo
            direccion = 'Av. Cra 68 #75-50, Bogota'; ventana = '08:00 - 11:00'; motivo = 'Cliente ausente' }
        Enviar $urlAvisos (Con-Actor $d 'Andres Molina' 'Conductor' 'voz') 'MAKE_WEBHOOK_URL'

        $d = [ordered]@{ tipo = 'stock_bajo'; sku = 'SKU-1001'; producto = 'Caja bebidas 12 und'
            stock_actual = 60; stock_minimo = 60; negativo = $false; pedido = $null; correo_destino = $correo }
        Enviar $urlAvisos (Con-Actor $d 'Carlos Rueda' 'Gestor logístico' 'pantalla') 'MAKE_WEBHOOK_URL'

        $d = [ordered]@{ tipo = 'solicitud_contacto'; cliente = 'Supermercado El Portal'; correo = $correo
            telefono = '3115550111'; motivo = 'Cambiar la ventana del pedido de hoy'; correo_destino = $correo }
        Enviar $urlAvisos (Con-Actor $d 'Marcela Rios' 'Cliente' 'voz') 'MAKE_WEBHOOK_URL'

        $d = [ordered]@{ tipo = 'accion_asistente'; id_auditoria = 1; usuario = 'Laura Gomez'; rol = 'Administrador'
            accion = 'Renombrar motivo de fallo'; entidad = 'configuracion'
            resumen = 'Voy a renombrar el motivo Cliente ausente a Cliente no se encontraba.'
            detalle = $detalle; resultado = 'exito'; correo_destino = $correo }
        Enviar $urlAvisos (Con-Actor $d 'Laura Gomez' 'Administrador' 'voz') 'MAKE_WEBHOOK_URL'
    }
    'estructura-privado' {
        # Todos los campos de los tres tipos del escenario privado en un solo aviso.
        $d = [ordered]@{
            tipo = 'estructura'
            # resumen_diario
            fecha = $fecha; correo_destino = $correo
            kpis = (Kpis)
            productos_bajo_minimo = @((Producto-Bajo))
            fallidos_para_reprogramar = @((Fallido 'Carlos Rueda' 'Gestor logístico' 'pantalla'))
            resumen_html = $resumenHtml
            # credencial_usuario
            nombre = 'Usuario de Prueba'; correo = $correo; contrasena = 'EjemploNoUsar-123'
            debe_cambiar_contrasena = $true
            indicaciones = 'Esta contraseña es temporal: al iniciar sesión el sistema le pedirá cambiarla por una propia antes de continuar.'
            url_acceso = $urlPublica; creado_por = 'Laura Gomez (Administrador)'; accion = 'creación'
            # restablecimiento_contrasena
            enlace = "$urlPublica/auth/restablecer/ejemplo-no-sirve"; vence_en_minutos = 30
        }
        Enviar $urlResumen (Con-Actor $d 'Carlos Rueda' 'Gestor logístico' 'pantalla') 'MAKE_WEBHOOK_RESUMEN_URL'
    }
    'resumen' {
        $d = [ordered]@{
            tipo = 'resumen_diario'; fecha = $fecha; fecha_hora = $fechaHora; correo_destino = $correo
            kpis = (Kpis)
            productos_bajo_minimo = @((Producto-Bajo))
            fallidos_para_reprogramar = @((Fallido $sistema $rolSistema 'automático'))
            resumen_html = $resumenHtml
            realizado_por = $sistema; rol_realizado_por = $rolSistema; origen = 'automático'
            realizado_por_publico = $rolSistema
        }
        Enviar $urlResumen $d 'MAKE_WEBHOOK_RESUMEN_URL'
    }
    'credencial' {
        $d = [ordered]@{ tipo = 'credencial_usuario'; nombre = 'Usuario de Prueba'; correo = $correo
            contrasena = 'EjemploNoUsar-123'; debe_cambiar_contrasena = $true
            indicaciones = 'Esta contraseña es temporal: al iniciar sesión el sistema le pedirá cambiarla por una propia antes de continuar.'
            url_acceso = $urlPublica
            creado_por = 'Laura Gomez (Administrador)'; accion = 'creación'; correo_destino = $correo }
        Enviar $urlCred (Con-Actor $d 'Laura Gomez' 'Administrador' 'voz') 'MAKE_WEBHOOK_CREDENCIALES_URL'
    }
    'restablecimiento' {
        $d = [ordered]@{ tipo = 'restablecimiento_contrasena'; nombre = 'Usuario de Prueba'; correo = $correo
            enlace = "$urlPublica/auth/restablecer/ejemplo-no-sirve"; vence_en_minutos = 30
            correo_destino = $correo }
        Enviar $urlRestab (Con-Actor $d 'Usuario de Prueba' 'Conductor' 'pantalla') 'MAKE_WEBHOOK_RESTABLECIMIENTO_URL'
    }
}
