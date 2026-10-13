# SGDS — Sistema de Gestión de Despachos

Implementación del proyecto **"Optimización de la gestión logística en transporte y
almacenamiento mediante análisis de datos y procesos inteligentes"**
(Universidad de San Buenaventura, Ingeniería de Sistemas).

## Stack

| Capa | Tecnología | Justificación en el documento |
|---|---|---|
| Arquitectura | Flask + patrón MVC | RNF3 — arquitectura web ligera (MVC) |
| Modelo | SQLAlchemy ORM | RNF4 — integrabilidad y escalabilidad |
| Base de datos | SQLite (desarrollo) → MySQL (piloto) | RNF4 — motor relacional intercambiable |
| Vista | Jinja2 + CSS mobile-first | RNF1 — interfaz responsiva para la flota |
| Seguridad | Flask-Login + PBKDF2-SHA256 + CSRF | RNF5 — cifrado de contraseñas y sesiones seguras |
| Ruteo | OSRM (servicio público, sin API key) | RF3 — evita programar el algoritmo desde cero |
| Mapa | Leaflet + OpenStreetMap | RF3 — visualización de la secuencia |
| Despliegue | Render / Railway | RNF3 — plataformas cloud gratuitas |

## Estructura (MVC explícito)

```
.
├── app/
│   ├── __init__.py        Application factory
│   ├── extensions.py      Instancias compartidas (db, login, csrf)
│   ├── models/            MODELO   — Usuario, Cliente, Producto, Pedido, Ruta, Inventario
│   ├── controllers/       CONTROLADOR — auth, admin, usuarios, pedidos, inventario, conductor, cliente, solicitudes, asistente, automatizacion
│   │   └── asistente_api/ Funciones de voz por rol: conductor, gestor, admin, cliente
│   ├── services/          Lógica de negocio — despacho, pedidos, inventario, planificación, ruteo, analítica, importador, seguimiento, solicitudes, búsqueda para voz, asistente, avisos
│   ├── asistentes/        Prompts de los agentes de Retell y su sincronización
│   ├── views/             VISTA    — plantillas Jinja2
│   └── static/            CSS y JS
├── migraciones/           Cambios de esquema aplicables sobre una base con datos
├── docs/                  Configuración generada de los agentes de Retell
├── pruebas/               1623 verificaciones automatizadas en 18 suites (MySQL)
├── ejemplos/              CSV de ejemplo para probar la importación
├── config.py              Configuración por entorno
├── run.py                 Punto de entrada y comandos CLI
├── seed.py                Datos de demostración e histórico
├── docker-compose.yml     MySQL para la fase piloto
├── Procfile / render.yaml Despliegue en Render o Railway
└── requirements.txt
```

## Puesta en marcha

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
.venv/bin/python seed.py      # crea el esquema y carga datos de demostración
.venv/bin/python run.py       # http://localhost:5001
```

### Comandos disponibles

```bash
.venv/bin/flask --app run init-db          # Crear el esquema
.venv/bin/flask --app run reset-db         # Borrar y recrear (solo desarrollo)
.venv/bin/flask --app run sembrar          # Cargar datos de demostración
.venv/bin/flask --app run migrar-clientes  # Normalizar clientes en una base con datos
.venv/bin/flask --app run migrar-asistente # Crear o actualizar las tablas del asistente de voz
.venv/bin/flask --app run migrar-configuracion # Crear la configuración del negocio (motivos, centro)
.venv/bin/flask --app run migrar-auditoria  # Crear la tabla de auditoría
.venv/bin/flask --app run migrar-pruebas-entrega # Pruebas de entrega de solo agregar (m007, MySQL)
.venv/bin/flask --app run sincronizar-asistentes # Crear o actualizar los agentes en Retell
.venv/bin/flask --app run documentar-asistentes  # Regenerar docs/configuracion_retell.md
```

## Usuarios de demostración

| Rol | Correo | Contraseña |
|---|---|---|
| Administrador | admin@sgds.com | Admin123* |
| Gestor logístico | despachador@sgds.com | Despacho123* |
| Conductor | conductor1@sgds.com | Conductor123* |
| Conductor | conductor2@sgds.com | Conductor123* |
| Cliente | cliente@sgds.com | Cliente123* |

## Trazabilidad de requisitos

| Req. | Descripción | Estado |
|---|---|---|
| RF1 | Gestión de usuarios y roles | ✅ Implementado |
| RF1.1 | Portal de seguimiento del cliente destinatario | ✅ Implementado |
| RF2 | Ingreso de órdenes de despacho (manual + CSV) | ✅ Implementado |
| RF3 | Generación de rutas con API de geolocalización | ✅ Implementado |
| RF4 | Actualización de estados en terreno | ✅ Implementado |
| RF5 | Sincronización lógica de inventario | ✅ Implementado |
| RF6 | Tablero de control con 3 KPIs | ✅ Implementado |
| RNF1 | Interfaz responsiva mobile-first | ✅ Implementado |
| RNF2 | Tiempos de respuesta 3–5 s | ✅ Medido e instrumentado |
| RNF3 | Arquitectura MVC y despliegue | ✅ Implementado |
| RNF4 | Integrabilidad (SQLite ↔ MySQL) | ✅ Implementado |
| RNF5 | Seguridad de acceso | ✅ Implementado |

## Modelo de datos: clientes y puntos de entrega

Los datos del destinatario vivían repetidos en cada fila de `pedidos`
(`cliente_nombre`, `cliente_telefono`, `direccion`, `ciudad`, `latitud`, `longitud`).
Esos atributos dependen del cliente, no de la orden, así que su lugar en `pedidos` era
una **dependencia transitiva**: el mismo destino se reescribía en cada despacho y
cualquier variación de escritura del ERP creaba un "cliente" distinto. Consultar los
pedidos de un cliente solo era posible comparando texto con `LIKE`.

### Esquema

```
usuarios ──0..1── clientes ──1..N── direcciones_cliente
                     │                      │
                     └──────1..N── pedidos ─┘
```

| Tabla | Contiene |
|---|---|
| `clientes` | Razón social, NIT, teléfono, correo y la cuenta de portal (opcional) |
| `direcciones_cliente` | Una fila por sede: dirección, ciudad, coordenadas y ventana habitual |

**`clientes.usuario_id` es nullable y único.** La entidad de negocio y la credencial de
acceso son cosas distintas: la mayoría de los clientes llega por la importación CSV y
nunca se registra en el portal, así que el cliente existe sin cuenta, y cuando se
registra se le vincula una. Poner la clave del lado de `Usuario` habría obligado a
inventar cuentas para poder cargar pedidos.

**Un cliente sostiene varias sedes.** Un cliente comercial atiende distintos puntos con
coordenadas y ventanas horarias propias; colapsarlas en `clientes` solo habría movido de
lugar la redundancia.

### Por qué `pedidos` conserva la dirección

Los campos de entrega **siguen en `pedidos`** junto a las nuevas claves ajenas. No es
redundancia por descuido: son el estado del destino **en el momento del despacho**. Si el
cliente traslada una sede, un pedido que solo apuntara por clave ajena mostraría una
dirección a la que nunca se fue, falseando la trazabilidad (`EventoPedido`,
`PruebaEntrega`) y los indicadores del RF6. Las claves ajenas sirven para consultar; la
copia histórica, para auditar. La suite de pruebas verifica exactamente esto: trasladar
la sede no altera los pedidos ya despachados.

### Deduplicación al cargar

Tanto el alta manual como el CSV pasan por [`app/services/clientes.py`](app/services/clientes.py),
que resuelve el cliente antes de crear la orden:

1. Si la fila trae **NIT**, ese es el criterio: reconoce al cliente aunque el ERP haya
   reescrito la razón social.
2. Si no, se compara el **nombre normalizado** — sin acentos, sin puntuación, sin
   mayúsculas ni espacios de más.

Así `Supermercado El Portal`, `SUPERMERCADO  EL PORTAL` y `Supermercado El Portál.`
resuelven al mismo registro. La vista previa de la importación marca con una etiqueta
**nuevo** los clientes que aún no existen, para que el despachador detecte un nombre mal
escrito *antes* de confirmar la carga.

## Administración de cuentas (RF1)

**Usuarios** (solo administrador) y **Clientes** (administrador y gestor logístico) son
las dos pantallas que cierran el RF1: antes las cuentas solo podían nacer en `seed.py`,
así que dar de alta un conductor o habilitar el portal de un cliente exigía entrar a la
base de datos a mano.

| Pantalla | Quién entra | Qué permite |
|---|---|---|
| `/admin/usuarios` | Administrador | Crear, editar, activar/desactivar cuentas y restablecer contraseñas |
| `/admin/clientes` | Administrador y gestor logístico | Directorio de clientes, sedes, y quién tiene portal |

### Habilitar el portal de un cliente

En **Clientes**, los que no tienen acceso muestran "Sin acceso" y un enlace *Crear cuenta*
que abre el formulario con el cliente ya seleccionado, el rol `CLIENTE` puesto y el nombre
y teléfono heredados. Al guardar, el sistema escribe `clientes.usuario_id`.

El desplegable de cliente **solo ofrece los clientes sin cuenta**, porque la relación es
1 a 0..1 y la columna es única: un cliente no puede quedar asociado a dos credenciales.
La comprobación se repite en el servidor, ya que un POST fabricado a mano no pasa por el
formulario renderizado. Y si a una cuenta se le cambia el rol a otro distinto de `CLIENTE`,
el vínculo se deshace: no queda una credencial con acceso al portal de un cliente al que
ya no representa.

### Contraseñas

Si el administrador deja el campo en blanco, el sistema genera una contraseña temporal con
`secrets` y **la muestra una sola vez** para que se entregue por un canal aparte. *Restablecer
clave* hace lo mismo sobre una cuenta existente. El usuario la cambia luego desde su perfil.

### Las cuentas no se eliminan

Solo se desactivan. `Usuario` es el origen de las claves ajenas de `rutas`,
`eventos_pedido` y `movimientos_inventario`: borrar una cuenta rompería la trazabilidad
que sostiene el RF6. El login ya rechaza a los usuarios inactivos, así que desactivar es
el equivalente funcional de eliminar sin perder el histórico. Lo mismo aplica a los
clientes, referenciados por `pedidos.cliente_id`.

### Salvaguardas

| Situación | Comportamiento |
|---|---|
| Un administrador intenta cambiarse el rol | Bloqueado: perdería la capacidad de revertirlo |
| Un administrador intenta desactivar su propia cuenta | Bloqueado, tanto editando como con la acción rápida |
| Se desactiva o degrada al último administrador activo | Bloqueado: debe quedar al menos uno |
| Se cambia el rol o se desactiva un conductor con rutas sin cerrar (PLANIFICADA o EN_CURSO) | Bloqueado: la ruta quedaría sin quien la cierre. Se eliminan las que no han salido o se espera a que se cierren |
| Un conductor sin rutas abiertas deja de serlo | Se permite; su vehículo queda sin conductor y la pantalla (o el resumen de voz) lo avisa |
| Una cuenta de cliente pasa a un rol interno, o una interna pasa a cliente | Bloqueado: son personas distintas, una externa y otra interna; se crea otra cuenta |
| Una cuenta de cliente cambia a otro cliente disponible | Se permite; el cliente anterior queda sin acceso al portal y se avisa |
| Se registra un cliente homónimo de otro y ninguno tiene NIT | Bloqueado: casi siempre es un duplicado por error de escritura |
| Se agrega una sede que ya existe escrita distinto | Se detecta por dirección normalizada y se rechaza |

## Portal del cliente

El cliente con cuenta entra en `/portal` y consulta el estado de sus órdenes: filtros por
pedidos en curso o cerrados, la bitácora de cada entrega y su constancia de recepción.

### Lo que el portal deliberadamente NO muestra

**No expone la entidad `Ruta`.** Una ruta agrupa las paradas de varios clientes e incluye
sus direcciones y teléfonos, el conductor asignado y la geometría completa del recorrido.
Mostrarle "sus rutas" a un cliente filtraría datos personales de terceros: es un problema
de privacidad, no de permisos. Lo que el cliente necesita —dónde va su pedido— se responde
con el estado de la orden y su bitácora, que ya se registran para el RF4.

La bitácora se **traduce** antes de mostrarse: el cliente ve "En camino a su dirección",
no el estado interno ni notas operativas como "Creado por importación CSV".

### Aislamiento de datos

Toda consulta del portal filtra por el `cliente_id` de la sesión, con el mismo criterio
que la vista del conductor: contra la clave ajena, nunca contra el nombre. Cambiar el id
en la URL devuelve **403**. Una cuenta con rol `CLIENTE` sin cliente asociado —un registro
a medio configurar— tampoco ve nada.

El aterrizaje tras el login se resuelve en `INICIO_POR_ROL`
([`app/controllers/seguridad.py`](app/controllers/seguridad.py)), única fuente de verdad.
Antes era un `if es_conductor / else tablero`, que habría enviado cualquier rol nuevo al
tablero administrativo para recibir un 403.

## Formato del CSV de importación (RF2)

Una fila por producto; las filas que comparten `codigo` se agrupan en un mismo pedido.
Descargue la plantilla desde **Pedidos → Importar CSV → Descargar plantilla**.

| Columna | Obligatoria | Formato |
|---|---|---|
| `cliente_nombre` | Sí | Texto |
| `cliente_documento` | No | NIT o cédula; identifica al cliente aunque cambie el nombre |
| `direccion` | Sí | Texto |
| `sku` | Sí | Debe existir en el inventario |
| `cantidad` | Sí | Entero mayor que cero |
| `codigo` | No | Se genera automáticamente si se omite |
| `cliente_telefono` | No | Texto |
| `ciudad` | No | Por defecto Bogotá |
| `latitud` / `longitud` | No | Decimal |
| `fecha_despacho` | No | AAAA-MM-DD, DD/MM/AAAA (por defecto hoy) |
| `ventana_inicio` / `ventana_fin` | No | HH:MM |
| `prioridad` | No | 1 alta, 2 media, 3 baja |
| `observaciones` | No | Texto |

Se aceptan separadores por coma, punto y coma o tabulación, y codificación UTF-8 o Latin-1.
El sistema muestra una vista previa antes de confirmar y reporta las filas inválidas
línea por línea, sin bloquear la carga de las filas correctas.

## Generación de rutas (RF3)

El sistema no programa un algoritmo de enrutamiento propio: delega el cálculo en
**OSRM** (Open Source Routing Machine), un servicio público que no requiere clave de API.

### Dos criterios de ordenamiento

| Criterio | Qué hace | Cuándo usarlo |
|---|---|---|
| **Optimizar distancia** | OSRM resuelve el orden de las paradas minimizando el recorrido total (servicio `/trip`) | Operación normal; produce el menor costo de combustible y tiempo |
| **Respetar ventanas horarias** | Ordena por hora límite del cliente y OSRM calcula la geometría real de ese orden (servicio `/route`) | Cuando hay clientes comerciales con ventanas estrictas que no admiten holgura |

En las pruebas sobre la ruta de demostración en Bogotá, la optimización por distancia
produjo **43.5 km** frente a **55.3 km** del orden por ventana horaria: un 21 % de
diferencia. Ese contraste es precisamente la tensión operativa que el documento
identifica entre eficiencia y "ventanas horarias estrictas fijadas por los clientes
comerciales" (numeral 1.4).

### Degradación ante fallos de conectividad

La limitación de "brechas de conectividad urbana" del numeral 1.4 se atiende con un
respaldo local: si OSRM no responde (sin red, timeout, o error del proveedor), el
sistema aplica una heurística de **vecino más cercano** sobre distancia haversine y
marca la ruta como calculada localmente, advirtiendo al usuario que las distancias son
estimaciones en línea recta. **La planificación nunca queda bloqueada.**

Los pedidos sin coordenadas no se pierden: quedan al final de la secuencia y el sistema
reporta cuántos son para que el despachador los geolocalice.

### Nota sobre TLS en macOS

El intérprete de Python que trae macOS está compilado contra **LibreSSL 2.8.3**, que no
puede negociar el handshake TLS del servidor público de OSRM. El servicio intenta primero
por HTTPS y, solo ante un error de SSL, reintenta por HTTP. En un despliegue sobre un
Python con OpenSSL moderno el primer intento tiene éxito y nunca se usa el canal sin
cifrar. Para producción, defina `URL_OSRM` en `.env` apuntando a una instancia propia:
el servidor público no ofrece garantías de disponibilidad.

Con `ENTORNO=produccion` el reintento por HTTP se desactiva por completo, aunque nunca
se dispare en la práctica: las coordenadas de los clientes no deben viajar sin cifrar.

## Ciclo de entrega y sincronización de inventario (RF4 + RF5)

La regla central del proyecto vive en [`app/services/despacho.py`](app/services/despacho.py):
**el inventario se descuenta única y exclusivamente cuando el conductor confirma la
entrega en terreno.** Esto conecta la última milla con la bodega y elimina la
descoordinación entre el stock real y las órdenes de despacho descrita en la cadena
causal 2.3.2.

### Máquina de estados

```
PENDIENTE ──► ASIGNADO ──► EN_RUTA ──► ENTREGADO   (descuenta inventario)
   │              ▲            │
   │              │            └────► FALLIDO       (NO descuenta inventario)
   │              │                      │
   │              └──────────────────────┘          (reintento)
   │              │
   └──────────────┴──────────────────────────────► CANCELADO  (anulación, exige motivo)
```

Las transiciones se validan en el modelo (`EstadoPedido.TRANSICIONES`). Un pedido
entregado es terminal: no admite reversión, lo que impide descontar el inventario dos
veces por un error de operación. `CANCELADO` es aparte: no es un intento de entrega
(no genera PoD ni toca el inventario), así que se valida y aplica por separado en
`app.services.despacho.anular_pedido`, permitido solo desde `PENDIENTE`, `ASIGNADO` o
`FALLIDO`.

### Garantías implementadas

| Situación | Comportamiento |
|---|---|
| El conductor pulsa "Entregar" dos veces (conectividad intermitente) | La bandera `inventario_descontado` hace la operación idempotente: el stock se afecta una sola vez |
| Entrega fallida | Se registra el motivo y la PoD, pero el inventario **no** se toca |
| Reintento tras un fallo | Vuelve a `EN_RUTA`; si luego se entrega, ahí sí descuenta |
| Stock insuficiente al entregar | La entrega **no se bloquea** (la mercancía ya salió físicamente): se descuenta, el stock queda negativo y el sistema alerta del descuadre para que el administrador lo concilie |
| Última parada cerrada | La ruta pasa automáticamente a `FINALIZADA` y registra la hora |
| Se anula un pedido | Exige motivo, pasa a `CANCELADO` (no se borra) y registra un `EventoPedido`; se excluye de la tasa de éxito, los pendientes y la productividad por conductor |
| Una ruta tiene paradas canceladas | Puede finalizarse igual: `CANCELADO` cuenta como estado definitivo para el avance y el cierre automático |
| Un conductor intenta operar la parada de otro | 403 |
| La sesión caduca con la pantalla abierta | Página en español explicando que expiró, en lugar del error crudo de Flask |
| Se desactiva una cuenta con la sesión ya abierta | Pierde el acceso en la siguiente petición, sin esperar a que vuelva a iniciar sesión |

Cada cambio de estado deja un `EventoPedido` con usuario, hora y coordenadas, y cada
descuento un `MovimientoInventario` que cita el pedido de origen: trazabilidad completa
en ambos sentidos.

### El stock solo cambia con movimientos

Fuera de las entregas, el stock de un producto cambia únicamente en *Inventario → producto
→ Registrar movimiento*, o por voz con `productos` (`entrada` o `ajuste`):

- **Entrada**: suma unidades recibidas (al menos una); el motivo es opcional.
- **Ajuste por inventario físico**: fija el stock al valor contado (puede ser cero) y el
  **motivo es obligatorio**; `registrar_movimiento` también lo exige, así que ningún
  camino deja un ajuste sin motivo.

El stock inicial solo se pide al crear el producto y entra como una entrada. El
formulario de edición muestra el stock actual pero no lo cambia: un `stock_actual` en ese
formulario se ignora.

### Geolocalización desde el teléfono del conductor

La limitación de "dependencia de hardware de terceros" (numeral 1.4) implica que no hay
dispositivos IoT ni GPS dedicados. La interfaz usa la API de geolocalización del
navegador para adjuntar las coordenadas a cada actualización de estado. **Si el conductor
niega el permiso o no hay señal, la acción se envía igual**: registrar el estado es lo
crítico en terreno; la ubicación es complementaria.

## Asistente de voz (Retell AI)

Cada rol (conductor, gestor logístico, administrador y cliente) puede abrir un asistente
de voz con el botón flotante del micrófono y su propio agente de Retell. **Es opcional:**
sin `RETELL_API_KEY`, o sin el agente de un rol, el botón no aparece para ese rol y la
aplicación funciona igual.

### Capacidades por rol

Todo lo que un rol hace en pantalla se puede hacer por voz, salvo las exclusiones de la
tabla siguiente. Las acciones de una entidad se agrupan en una sola función con el
parámetro `operacion`; **(M)** marca las operaciones que modifican datos (piden
confirmación y quedan en la auditoría).

| Rol | Funciones | Total |
|---|---|---|
| **Conductor** | Consultas: `mi-ruta`, `siguiente-parada`, `detalle-parada`, `mi-historial` (últimas 5 rutas). `iniciar-ruta` (M). `parada`: `en-camino` (M, también reintenta una fallida), `fallo` (M, con el catálogo o "Otro"), `entregar` (M, con nombre y documento de quien recibe) | 6 |
| **Gestor logístico** | Consultas: `resumen-dia`, `pendientes-sin-ruta`, `avance-rutas`, `buscar-pedido`, `stock-producto`, `productos-bajo-minimo`, `fallidos-hoy`, `solicitudes-contacto-pendientes`. Analítica, con los periodos del panel: `tiempo-promedio-entrega`, `cumplimiento-ventana`, `productividad-conductores`, `causas-fallo`. `atender-solicitud` (M). `pedidos`: crear, editar, anular, reintentar (M). `productos`: crear, editar, activar, desactivar, entrada, ajuste (M). `rutas`: consultar, crear, agregar-parada, quitar-parada, recalcular, eliminar (M salvo consultar). `clientes` y `sedes`: consultar, crear, editar, activar, desactivar (M salvo consultar). `vehiculos`: consultar, crear, editar, activar, desactivar (M salvo consultar) | 19 |
| **Administrador** | Todas las del gestor; `resumen-rendimiento` (el panel de rendimiento, solo suyo como en pantalla); `usuarios`: consultar, crear, editar, activar, desactivar, restablecer-contrasena (M salvo consultar); `configuracion`: consultar, editar-centro, agregar-motivo, renombrar-motivo, activar-motivo, desactivar-motivo (M salvo consultar); `bitacora` (últimos registros, con filtros por fecha, usuario y entidad) | 23 |
| **Cliente** | Consultas: `pedidos-en-curso`, `pedidos-recientes`, `estado-pedido`, `ventana-entrega`, `motivo-fallo`, `mis-sedes`. Acciones: `cancelar-pedido` (M, solo PENDIENTE), `solicitar-contacto` (M, una pendiente a la vez) | 8 |

Reglas que el servidor aplica igual que la pantalla, porque usa los mismos servicios
(`pedidos`, `despacho`, `planificacion`, `inventario`, `clientes`, `vehiculos`,
`usuarios`, `configuracion`, `solicitudes`):

- **Pedidos.** Crear exige cliente y sede registrados; si no existen, Gaby ofrece
  registrarlos y retoma el pedido con lo ya dicho. Editar un PENDIENTE cambia fecha (no
  anterior a hoy), sede del mismo cliente (actualiza la copia de la dirección), ventana,
  prioridad, observaciones y productos; un ASIGNADO, solo ventana, prioridad y
  observaciones, y el resumen advierte recalcular la ruta. Los demás estados no se editan.
- **Rutas.** El resumen de crear lee conductor, vehículo, número de paradas y criterio;
  el de eliminar, cuántas paradas vuelven a PENDIENTE. Una ruta con entregas no se
  elimina.
- **Inventario.** El stock solo cambia con `entrada` o `ajuste` (motivo obligatorio, y el
  resumen lee el stock actual y el nuevo). Con stock inicial al crear, el resumen avisa
  que el SKU queda fijo.
- **Datos fijos.** El NIT se registra una vez; el SKU con movimientos, la placa y el
  código de pedido no cambian; el correo de inicio de sesión solo lo cambia un
  administrador en pantalla.
- **Usuarios.** Las contraseñas nunca se dicen: al crear o restablecer, el servidor
  genera una y la envía solo en `credencial_usuario`, por su propio webhook, al correo
  del usuario. Sin `MAKE_WEBHOOK_CREDENCIALES_URL`, esas dos operaciones se rechazan ya en
  el resumen. El resumen de editar lee las consecuencias (un vehículo que queda sin
  conductor, un cliente que pierde el portal); ver [Salvaguardas](#salvaguardas).
- **Entregas.** El conductor confirma una entrega por voz solo con el nombre y el
  documento de quien recibe; descuenta el inventario igual que la pantalla. La llamada
  no envía la ubicación del teléfono, así que la prueba de entrega queda sin
  coordenadas y con la observación *"Registrada por el asistente de voz, sin
  ubicación"*. Iniciar la ruta, en camino, entregar y fallo quedan en la auditoría sin
  el correo `accion_asistente`: son de rutina y su historial está en los eventos.
- **Pruebas de entrega de solo agregar.** Cada cierre (entrega o intento fallido)
  agrega su prueba: un pedido que falla, se reintenta y se entrega conserva todas. El
  pedido, el portal, el aviso FALLIDO y el resumen diario usan la más reciente; las
  causas de fallo cuentan todos los intentos fallidos, y el cumplimiento de ventana, solo
  la entrega. En una base existente, aplique `flask --app run migrar-pruebas-entrega`
  (m007, idempotente): quita el índice único de `pruebas_entrega.pedido_id` y conserva
  las filas.

Las ediciones son parciales: solo cambia lo que el usuario dijo, y el resumen lee cada
cambio con su valor anterior y el nuevo. La operación entra en la huella de la
confirmación, así que confirmar una no ejecuta otra, y la auditoría la registra con su
propio título (*"Ajustar inventario"*). En la bitácora de cada pedido las acciones por voz
dejan la nota *"Registrado por el asistente de voz"*. La lista completa de funciones, con
sus parámetros, está en [docs/configuracion_retell.md](docs/configuracion_retell.md).

### Lo que queda fuera de la voz, y por qué

| Rol | Excluido | Por qué |
|---|---|---|
| Todos | Iniciar y cerrar sesión, cambiar la propia contraseña, el botón que abre la llamada | La llamada parte de una sesión iniciada; las contraseñas nunca se dicen por voz |
| Gestor y admin | Importar pedidos desde CSV | Exige subir un archivo; no tiene equivalente hablado |
| Admin | Cambiar el correo de inicio de sesión de una cuenta | Es la identidad de la cuenta: solo en pantalla |
| Conductor | La ubicación del teléfono y la observación escrita al entregar o registrar un fallo | La llamada no envía la ubicación; la prueba de entrega por voz lo dice en su observación |
| Conductor | Rutas de otros días (salvo su historial) o de otros conductores | Igual que en pantalla: solo opera su ruta de hoy |
| Cliente | Cancelar un pedido ya programado | Desde ASIGNADO ya hay una ruta planificada: el asistente ofrece solicitar contacto con el gestor |
| Cliente | Rutas, conductores, stock, datos de otros clientes | El mismo aislamiento del portal: un pedido ajeno se responde igual que uno inexistente |

**La cobertura está cerrada.** `app/asistentes/cobertura.py` asigna a cada ruta `POST` de
la pantalla su función de voz o una de las exclusiones de esta tabla, y la suite 16 falla
si aparece una ruta nueva sin ninguna de las dos (`PENDIENTES_PERMITIDOS = False`).

### Cómo funciona

1. El navegador pide permiso para el micrófono. Si el usuario lo niega, se le explica
   cómo habilitarlo y no se crea ninguna llamada.
2. `POST /asistente/llamada` (sesión iniciada y token CSRF) elige el agente según el rol
   (403 si el rol no tiene), crea la llamada con `retell-sdk` en `/v3/create-web-call`
   pasando como variables dinámicas `nombre_usuario` y `fecha_hoy` (hora de Bogotá), y
   guarda `call_id → usuario` y el rol en `sesiones_asistente` con una vigencia de
   **15 minutos**.
3. El navegador se une a la llamada con el SDK web. **La API key nunca sale del
   servidor:** el navegador solo recibe `access_token`, `call_id`, `transport` e
   `ice_servers`, que el transporte "gateway" de v3 necesita para conectarse.
4. Durante la conversación, Retell invoca las *custom functions*, todas `POST` en
   `/api/asistente/<espacio>/<función>` (`conductor`, `gestor`, `admin`, `cliente`). Un
   único `before_request` las protege:
   - la función debe existir en el registro → **404** si no;
   - verifica `X-Retell-Signature` sobre el cuerpo crudo con el método del SDK
     (HMAC con la API key, que además rechaza firmas de más de 5 minutos) → **401** si
     falla;
   - identifica al usuario por `call.call_id` → **403** si no existe, venció, la cuenta
     fue desactivada o cambió de rol después de abrir la llamada;
   - el rol de la sesión debe estar entre los que la función declara → **403** si no:
     una sesión de un rol no puede llamar las funciones de otro. El administrador usa
     las del gestor por su misma URL.
5. Cada función responde `{"mensaje": "..."}`: una frase breve en español para leer en
   voz alta. Un error de negocio (pedido inexistente, transición no permitida) responde
   200 con la explicación, para que el agente se la diga al usuario.

Cada función se declara con `funcion_asistente` (`app/controllers/asistente_api/`), que
registra sus roles permitidos, su descripción y sus parámetros. Ese registro es la única
fuente de las herramientas que se configuran en Retell.

### Confirmación de acciones

Toda función que modifica datos se ejecuta en **dos llamadas**, y la confirmación la
controla el servidor, no el agente:

1. Sin `confirmar=true`, la función valida, **no cambia nada** y responde un resumen que
   termina en *"¿confirmas?"*. El servidor guarda en la sesión la huella de la función
   y sus argumentos. Si la acción no es posible (una transición inválida, por ejemplo),
   responde el motivo y no pide confirmación.
2. Con `confirmar=true` solo ejecuta si **esa misma función con exactamente los mismos
   argumentos** se resumió antes en **esta llamada**, hace **menos de 3 minutos**. La
   confirmación se consume en la misma transacción que la acción, así que no sirve dos
   veces. En cualquier otro caso vuelve a resumir sin ejecutar.

Solo queda pendiente un resumen por llamada: pedir otra acción reemplaza el anterior.
Así, que el LLM mande `confirmar=true` de entrada no basta para cambiar datos. Los
resúmenes leen lo que va a pasar: crear un pedido lee cliente, sede, fecha (*"martes 6
de octubre"*), prioridad y cada producto con su cantidad; reintentar un fallido avisa si
la ruta finalizada se va a reabrir.

### Búsqueda para voz

`app/services/busqueda_voz.py` resuelve lo que el usuario dice, con la misma
normalización que deduplica clientes: un pedido por su código dictado (con o sin guiones),
por su número del día (*"el pedido 5 de hoy"*) o por el nombre del cliente; un producto
por SKU o por nombre aproximado (*"arros"*, *"detergente dos kilos"*); un cliente, una
sede o un conductor por nombre. Si hay varias coincidencias, la respuesta lista hasta
cinco y termina en *"¿Cuál?"*, para que el agente pregunte.

### Solicitudes de contacto

Un cliente puede pedir por voz que el gestor lo contacte. La solicitud queda en
`solicitudes_contacto` con el teléfono y el correo del cliente, y se avisa a Make con el
evento `solicitud_contacto` (ver [Automatizaciones con Make](#automatizaciones-con-make)).
Mientras un cliente tenga una pendiente, no se registra otra ni se avisa de nuevo. El
gestor y el administrador las ven en **Solicitudes** (`/admin/solicitudes/`) o las
consultan por voz, y las marcan como atendidas en pantalla o por voz
(`atender-solicitud`, por el nombre del cliente).

### Configurar los agentes en Retell: `sincronizar-asistentes`

Los prompts, los mensajes de bienvenida y las funciones de cada rol están en el
repositorio (`app/asistentes/`), y un comando los aplica en Retell por API:

1. En `.env`, defina:
   - `RETELL_API_KEY`: **la API key que tiene el distintivo de webhook** en el panel de
     Retell (es la que firma las funciones).
   - `URL_PUBLICA`: la URL `https://` pública del servidor (ngrok o Render), sin barra
     final.
   - `RETELL_VOZ_ID`: una voz en español del panel de Retell (*Voices*). Solo se usa al
     crear agentes nuevos; si se define, también se aplica a los existentes.
   - `RETELL_AGENTE_CONDUCTOR_ID` y los demás `RETELL_AGENTE_*_ID`, si ya existen.
2. Ejecute:

   ```bash
   .venv/bin/flask --app run sincronizar-asistentes
   ```

   Para cada rol crea el agente, o lo actualiza si ya existe (por su variable o por su
   nombre, *SGDS - Gestor logistico* por ejemplo, para no duplicarlo). Lo deja con
   español latino (`es-419`), **duración máxima de 10 minutos** (la sesión del servidor dura
   15, así una llamada nunca sobrevive a su sesión) y **fin tras 20 segundos de
   silencio**, y con sus funciones en `POST`, sin la opción de enviar solo los argumentos
   (el servidor necesita el objeto `call` para leer el `call_id`) y con *Talk While
   Waiting*: mientras una función responde, el agente dice *"Un momento, lo reviso."*
3. Copie al `.env` (y a las variables del despliegue) los `agent_id` que imprime para
   los agentes nuevos.

**Al cambiar la URL de ngrok basta con volver a ejecutar el comando.** Con
`--rol cliente` (repetible: `--rol gestor --rol admin`) sincroniza solo esos agentes.
Cada llamada a Retell tiene 60 segundos de timeout (el botón del asistente usa 10), y si
un agente termina en *"Request timed out"* el comando repite una vez su sincronización
completa: como vuelve a leer el agente en Retell, reutiliza lo que sí se haya aplicado
en vez de duplicarlo.

Detalles del versionado de Retell, que el comando respeta: una versión publicada no se
puede editar, las llamadas web usan la última publicada, y **el agente y su Retell LLM
comparten el número de versión** (la versión N del agente usa la versión N de su LLM).
Por eso, si la última versión de un agente está publicada, el comando crea un borrador
a partir de ella (Retell crea a la vez la misma versión de su LLM), actualiza ese LLM en
la versión del borrador con la configuración del repositorio, y publica. Si la última
versión ya es un borrador (por ejemplo, de un intento que falló), la reutiliza. Nunca
crea un LLM nuevo para un agente existente, así que no deja LLM huérfanos, y las
versiones anteriores quedan intactas para volver a ellas desde el panel. Si un agente
existente usa un *conversation flow* en vez de un Retell LLM, el comando no lo toca y lo
reporta: configúrelo a mano o vacíe su variable para crear uno nuevo.

`flask --app run documentar-asistentes` regenera
[docs/configuracion_retell.md](docs/configuracion_retell.md), con el prompt, la
bienvenida y cada función (ruta y JSON de parámetros) de los cuatro agentes, para
revisarla o cargarla a mano en el panel. Una prueba verifica que esté al día.

En una base que ya tiene datos, cree o actualice las tablas del asistente con
`.venv/bin/flask --app run migrar-asistente` (idempotente): crea `sesiones_asistente` y
`solicitudes_contacto` si faltan y agrega las columnas de la confirmación a una tabla de
sesiones anterior. `init-db` y `reset-db` las crean completas por su cuenta.

### ngrok

Retell debe poder llegar al servidor, y el navegador solo permite usar el micrófono en
`https://` o en `localhost`:

```bash
.venv/bin/python run.py          # http://localhost:5001
ngrok http 5001                  # en otra terminal
```

Ponga la URL `https://….ngrok-free.app` en `URL_PUBLICA`, ejecute
`sincronizar-asistentes` y ábrala también en el teléfono del conductor. En el plan
gratuito esa URL cambia cada vez que se reinicia ngrok.

### Make

Las acciones por voz disparan los mismos avisos que la pantalla (`pedido_estado`,
`stock_bajo`), y las solicitudes de contacto el evento `solicitud_contacto`. La
configuración de los escenarios y la demo están en
[Automatizaciones con Make](#automatizaciones-con-make).

### SDK web: migración pendiente

El botón usa `RetellWebClient` de `retell-client-js-sdk` **3.0.2** (fijado en
`app/static/js/asistente.js`). Esa clase está marcada como obsoleta y **se elimina en la
versión 4.0 del SDK web**, así que habrá que migrar antes de actualizarlo. Hoy es la única
clase del SDK que se une a una llamada creada en el servidor: la nueva (`RetellClient`)
crea la llamada desde el navegador con la API key, algo que este diseño descarta.

El fin de `/v2/create-web-call` (18 de octubre de 2026) no afecta: `retell-sdk` 6.1.1 ya
llama a `/v3/create-web-call`, y el SDK web acepta su token si se le indica el
transporte `gateway` con el `call_id`.

Cambiar de página corta la llamada; el asistente está pensado para usarse desde una
misma pantalla sin navegar.

## Auditoría y bitácora

La tabla `auditoria` responde quién hizo qué, desde dónde (`pantalla`, `voz` o
`automático`) y con qué resultado. Es de **solo agregar**: el ORM rechaza modificar o borrar
un registro. Nunca guarda contraseñas ni tokens.

| Qué | Se audita | Correo `accion_asistente` a operaciones |
|---|---|---|
| Acciones confirmadas por voz (también las que fallan) | Sí, con el `call_id` | Sí, salvo el flujo del conductor (iniciar ruta, en camino, entregar, fallo) y las entradas de mercancía |
| *Usuarios* y *Configuración*, en pantalla | Sí | Sí |
| Clientes, sedes, productos, ajustes de inventario, vehículos, rutas (crear, recalcular, quitar parada, eliminar), pedidos (crear, editar, anular) y solicitudes atendidas, en pantalla | Sí | No |
| Importar pedidos desde CSV | Una línea por importación, con cuántos se crearon | No |
| El flujo del conductor y las entradas de mercancía, en pantalla | No: ya quedan en `eventos_pedido` y `movimientos_inventario`, con quién lo hizo | No |

Las ediciones registran solo los campos que cambiaron, con el valor anterior y el nuevo;
guardar sin cambios no deja registro. El administrador consulta todo en **Bitácora**
(`/admin/bitacora/`), con filtros por fecha, usuario, origen y acción. Para tenerla en
Google Sheets, el [escenario 3](#escenario-3-auditoría-en-google-sheets) la lee por lotes
sin perder ni repetir registros.

## Automatizaciones con Make

La aplicación se integra con [Make](https://www.make.com) en ambos sentidos, todo
opcional: **avisa** a un webhook de Make cuando ocurre algo (escenario 1), envía las
contraseñas de las cuentas creadas por voz por un **webhook aparte** (escenario de
credenciales), y **expone** un resumen diario (escenario 2) y la auditoría por lotes para
Google Sheets (escenario 3), que Make consulta de forma programada. Sin las variables
correspondientes, la aplicación funciona igual y no sale nada.

### Variables

| Variable | Para qué | Si está vacía |
|---|---|---|
| `MAKE_WEBHOOK_URL` | URL del *Custom webhook* que recibe los avisos | No se envía ningún aviso |
| `MAKE_WEBHOOK_KEY` | API key de los webhooks; viaja en el encabezado `x-make-apikey` | Los avisos salen sin el encabezado |
| `MAKE_WEBHOOK_CREDENCIALES_URL` | URL del webhook **aparte** que recibe solo `credencial_usuario` | No se envían contraseñas, y crear o restablecer cuentas **por voz** se rechaza en el resumen (en pantalla funciona igual) |
| `CORREO_OPERACIONES` | Destino de los avisos internos (`stock_bajo`, `solicitud_contacto`, `accion_asistente`) y del resumen diario | No se envían avisos internos; el resumen responde `correo_destino: null` |
| `URL_PUBLICA` | Dirección de acceso que lleva `credencial_usuario` (`url_acceso`) | `url_acceso` va vacía |
| `AUTOMATIZACION_TOKEN` | Token con el que Make consulta el resumen diario y la auditoría | Ambos endpoints responden **404** |

Genere el token con `python -c "import secrets; print(secrets.token_urlsafe(32))"`.

### Eventos salientes

Todo aviso es un `POST` JSON a `MAKE_WEBHOOK_URL` con un campo **`tipo`** (salvo
`credencial_usuario`, que va solo a `MAKE_WEBHOOK_CREDENCIALES_URL`), y todos usan el mismo
mecanismo:

- Solo sale **después del commit**: un cambio revertido no genera correo.
- Va en segundo plano, con un timeout de 3 s de conexión y 5 s de respuesta. **Si Make
  falla o tarda, la operación no se bloquea ni se revierte**; el fallo queda solo en el log
  y el aviso no se reintenta (por eso la hoja de auditoría se llena con el escenario 3, que
  sí recupera lo que falte).
- Todo evento lleva `hora` (`2026-10-06 11:20`) y **`fecha_hora`** en ISO 8601 con segundos
  y el desplazamiento de Bogotá (`2026-10-06T11:20:37-05:00`).

| `tipo` | Cuándo | Destinatario | Campos |
|---|---|---|---|
| `pedido_estado` | Un pedido pasa a **EN_RUTA, ENTREGADO o FALLIDO** desde cualquier origen (vista móvil, "Iniciar ruta" o asistente de voz), o se **anula** (CANCELADO) | El cliente, en `correo`. Si no tiene correo, no se envía | `codigo`, `estado`, `cliente`, `correo`, `direccion`, `ventana`, `hora`, `fecha_hora`; en FALLIDO (motivo de la prueba de entrega) y en CANCELADO (motivo de la anulación), además `motivo` |
| `stock_bajo` | Una entrega o un movimiento manual **cruza** un umbral del stock de un producto activo (ver abajo) | Operaciones, en `correo_destino` (`CORREO_OPERACIONES`) | `sku`, `producto`, `stock_actual`, `stock_minimo`, `negativo`, `pedido` (código del pedido entregado, o `null` si fue manual), `correo_destino`, `hora`, `fecha_hora` |
| `solicitud_contacto` | Un cliente pide por el asistente de voz que el gestor lo contacte (una pendiente a la vez por cliente) | Operaciones, en `correo_destino` (`CORREO_OPERACIONES`) | `cliente`, `correo`, `telefono`, `motivo`, `hora`, `fecha_hora`, `correo_destino` |
| `accion_asistente` | Se agrega a la **auditoría** un registro con correo: una acción confirmada por voz (con su resultado), salvo el flujo del conductor y las entradas de mercancía, o una acción de *Usuarios* o *Configuración* (ver [Auditoría](#auditoría-y-bitácora)) | Operaciones, en `correo_destino` (`CORREO_OPERACIONES`) | `id_auditoria`, `usuario`, `rol`, `accion`, `entidad`, `resumen`, `detalle` (texto plano, una línea por dato), `resultado` (`exito` o `error`), `hora`, `fecha_hora`, `correo_destino`. **Nunca lleva contraseñas ni tokens** |
| `credencial_usuario` | Se **crea o restablece por voz** una cuenta: la contraseña se genera en el servidor | El propio usuario, en `correo_destino` (su correo de inicio de sesión) | `nombre`, `correo`, `contrasena`, `url_acceso` (`URL_PUBLICA`), `creado_por` (nombre y rol), `accion` (`creación` o `restablecimiento`), `hora`, `fecha_hora`, `correo_destino`. Es el **único** lugar por el que viaja la contraseña: no se guarda en la auditoría ni sale en otro evento, y va **solo** a `MAKE_WEBHOOK_CREDENCIALES_URL`. En pantalla la contraseña se sigue mostrando una vez, como antes, y no se envía |

**Todo evento lleva además quién originó la acción:**

| Campo | Contenido |
|---|---|
| `realizado_por` | Nombre completo de quien hizo la acción, o `Sistema SGDS (automático)` |
| `rol_realizado_por` | `Conductor`, `Gestor logístico`, `Administrador`, `Cliente` o `Sistema` |
| `origen` | `pantalla`, `voz` o `automático` |
| `realizado_por_publico` | Solo la etiqueta del rol. Es el que deben usar los correos a clientes (`pedido_estado`): no exponen nombres del personal |

En `stock_bajo` es quien hizo el movimiento que cruzó el umbral (en una entrega, el
conductor que la confirmó). El resumen diario y cada fila de reprogramación salen como
`Sistema SGDS (automático)`, con origen `automático`.

`stock_bajo` avisa **una vez por umbral, al cruzarlo hacia abajo**, y no en cada
movimiento posterior:

- **Mínimo**: el stock pasa de estar sobre el mínimo a quedar en o por debajo
  (`previo > stock_minimo >= nuevo`), con `negativo: false`.
- **Cero**: el stock pasa a ser negativo (`previo >= 0 > nuevo`), con `negativo: true`. Un
  stock negativo revela un descuadre entre el inventario registrado y el físico.
- Si un mismo movimiento cruza los dos, sale **un solo aviso** con `negativo: true`.
- Tras reponer el stock por encima del umbral, un nuevo cruce vuelve a avisar.
- Cambiar el `stock_minimo` de un producto no es un movimiento de stock, así que no avisa.

```json
{
  "tipo": "pedido_estado",
  "codigo": "PED-20261006-001",
  "estado": "CANCELADO",
  "cliente": "Supermercado El Portal",
  "correo": "compras@ejemplo.com",
  "direccion": "Av. Cra 68 #75-50, Bogota",
  "ventana": "08:00 - 11:00",
  "hora": "2026-10-06 09:12",
  "fecha_hora": "2026-10-06T09:12:00-05:00",
  "motivo": "El cliente cancelo la compra",
  "realizado_por": "Carlos Rueda",
  "rol_realizado_por": "Gestor logístico",
  "origen": "pantalla",
  "realizado_por_publico": "Gestor logístico"
}
```

```json
{
  "tipo": "stock_bajo",
  "sku": "SKU-1001",
  "producto": "Caja bebidas 12 und",
  "stock_actual": 60,
  "stock_minimo": 60,
  "negativo": false,
  "pedido": null,
  "correo_destino": "operaciones@ejemplo.com",
  "hora": "2026-10-06 10:40",
  "fecha_hora": "2026-10-06T10:40:00-05:00",
  "realizado_por": "Carlos Rueda",
  "rol_realizado_por": "Gestor logístico",
  "origen": "voz",
  "realizado_por_publico": "Gestor logístico"
}
```

```json
{
  "tipo": "solicitud_contacto",
  "cliente": "Supermercado El Portal",
  "correo": "compras@ejemplo.com",
  "telefono": "3115550111",
  "motivo": "Cancelar el pedido de hoy",
  "hora": "2026-10-06 11:05",
  "fecha_hora": "2026-10-06T11:05:00-05:00",
  "correo_destino": "operaciones@ejemplo.com",
  "realizado_por": "Marcela Rios",
  "rol_realizado_por": "Cliente",
  "origen": "voz",
  "realizado_por_publico": "Cliente"
}
```

```json
{
  "tipo": "accion_asistente",
  "id_auditoria": 1842,
  "usuario": "Carlos Rueda",
  "rol": "Gestor logístico",
  "accion": "Anular pedido",
  "entidad": "pedido",
  "resumen": "Voy a anular el pedido PED-20261006-004 de Tienda La Esquina por: Duplicado.",
  "detalle": "funcion: gestor/pedidos:anular\ndatos / operacion: anular\ndatos / pedido: PED-20261006-004\ndatos / motivo: Duplicado\nrespuesta: Listo, anulé el pedido PED-20261006-004.",
  "resultado": "exito",
  "hora": "2026-10-06 11:20",
  "fecha_hora": "2026-10-06T11:20:00-05:00",
  "correo_destino": "operaciones@ejemplo.com",
  "realizado_por": "Carlos Rueda",
  "rol_realizado_por": "Gestor logístico",
  "origen": "voz",
  "realizado_por_publico": "Gestor logístico"
}
```

```json
{
  "tipo": "credencial_usuario",
  "nombre": "Lucia Herrera",
  "correo": "lucia.herrera@ejemplo.com",
  "contrasena": "q7RmT2xKp9Wd",
  "url_acceso": "https://sgds.ejemplo.com",
  "creado_por": "Laura Gomez (Administrador)",
  "accion": "creación",
  "hora": "2026-10-06 11:30",
  "fecha_hora": "2026-10-06T11:30:00-05:00",
  "correo_destino": "lucia.herrera@ejemplo.com",
  "realizado_por": "Laura Gomez",
  "rol_realizado_por": "Administrador",
  "origen": "voz",
  "realizado_por_publico": "Administrador"
}
```

### Escenario 1: webhook con router

1. Cree un escenario con el disparador **Webhooks → Custom webhook** y copie su URL en
   `MAKE_WEBHOOK_URL`. Si le asigna una *API key* al webhook, cópiela en
   `MAKE_WEBHOOK_KEY`: Make rechazará los avisos que no la traigan.
2. Pulse *Redetermine data structure* y provoque un evento de cada tipo (ver la demo más
   abajo) para que Make aprenda todos los campos.
3. Agregue un **Router** con una ruta por tipo, cada una con un filtro sobre `tipo`:
   - `tipo` = `pedido_estado` → módulo de correo (Gmail, Outlook o *Email → Send an
     email*) para `{{correo}}`. Para un texto distinto por estado, anide otro Router con
     un filtro por `estado`; en las ramas de FALLIDO y CANCELADO incluya `{{motivo}}`.
   - `tipo` = `stock_bajo` → correo para `{{correo_destino}}`. Use `negativo` para
     distinguir en el asunto un stock bajo de un descuadre.
   - `tipo` = `solicitud_contacto` → correo para `{{correo_destino}}` con el cliente,
     su teléfono, su correo y el motivo, para que el gestor lo llame.
   - `tipo` = `accion_asistente` → correo para `{{correo_destino}}` con
     `{{realizado_por}}`, `{{rol}}`, `{{origen}}`, `{{resumen}}`, `{{detalle}}` y
     `{{resultado}}`. La hoja de Google Sheets **no** se llena desde aquí sino con el
     escenario 3, que no pierde registros.
   - En los correos a clientes use `{{realizado_por_publico}}`, nunca `{{realizado_por}}`.

   Este escenario **no** tiene ruta para `credencial_usuario`: ese evento nunca llega a
   `MAKE_WEBHOOK_URL`. Si su escenario ya tenía esa ruta, elimínela.

### Escenario de credenciales: webhook aparte

`credencial_usuario` lleva una contraseña en claro, así que tiene su propio escenario, con
acceso restringido y sin historial del contenido:

1. Cree un escenario **separado** con el disparador **Webhooks → Custom webhook** y copie su
   URL en `MAKE_WEBHOOK_CREDENCIALES_URL`. Lleva la misma API key, `MAKE_WEBHOOK_KEY`.
2. En *Scenario settings* active **"Data is confidential"**: Make deja de guardar el
   contenido de cada ejecución, y la contraseña no queda en el historial del escenario.
3. Un único módulo de correo para `{{correo_destino}}` (el propio usuario) con
   `{{url_acceso}}`, `{{correo}}` y `{{contrasena}}`, y un texto que le sugiera cambiarla
   en *Perfil*. No agregue otros destinatarios, hojas ni registros.
4. Compártalo solo con quien administre las cuentas.

Sin `MAKE_WEBHOOK_CREDENCIALES_URL`, Gaby no ofrece crear ni restablecer cuentas por voz:
responde, ya en el resumen, que se hace en pantalla.

### Escenario 2: resumen diario programado

`GET /api/automatizacion/resumen-diario` devuelve el estado del día (`hoy()` en hora de
Bogotá):

- Se autentica con el encabezado **`X-Automatizacion-Token`**, comparado en tiempo
  constante contra `AUTOMATIZACION_TOKEN`. Responde **401** si falta o no coincide, y
  **404** si la variable no está configurada.
- `kpis` sale de las mismas funciones que el tablero y la analítica, así que el correo y
  la pantalla siempre muestran las mismas cifras. `cumplimiento_ventana` es `null` si hoy
  no hubo entregas con ventana horaria.
- `fallidos_para_reprogramar` lista los pedidos FALLIDOS de hoy cuyo cliente tiene correo.
- `resumen_html` es el correo ya armado en español, con estilos en línea y los datos de
  clientes escapados.

```json
{
  "fecha": "2026-10-06",
  "fecha_hora": "2026-10-06T18:00:04-05:00",
  "correo_destino": "operaciones@ejemplo.com",
  "kpis": {
    "total": 17, "entregados": 7, "fallidos": 5, "cancelados": 2, "pendientes": 3,
    "tasa_exito": 58.3, "cumplimiento_ventana": 100.0
  },
  "productos_bajo_minimo": [
    {"sku": "SKU-1004", "producto": "Aceite vegetal 1 L", "stock_actual": 30,
     "stock_minimo": 45, "negativo": false}
  ],
  "fallidos_para_reprogramar": [
    {"codigo": "PED-20261006-005", "cliente": "Supermercado El Portal",
     "correo": "compras@ejemplo.com", "direccion": "Av. Cra 68 #75-50, Bogota",
     "motivo": "Cliente ausente", "realizado_por": "Sistema SGDS (automático)",
     "rol_realizado_por": "Sistema", "origen": "automático", "realizado_por_publico": "Sistema"}
  ],
  "resumen_html": "<div style=\"font-family:Arial…\">…</div>",
  "realizado_por": "Sistema SGDS (automático)",
  "rol_realizado_por": "Sistema",
  "origen": "automático",
  "realizado_por_publico": "Sistema"
}
```

Configuración del escenario:

1. Disparador **Schedule** una vez al día, por ejemplo a las 18:00. Fije la zona horaria
   del escenario en *America/Bogota*.
2. Módulo **HTTP → Make a request**: método `GET`, URL
   `https://<su-dominio>/api/automatizacion/resumen-diario`, encabezado
   `X-Automatizacion-Token` con el valor de `AUTOMATIZACION_TOKEN` y *Parse response*
   activado. En el plan gratuito de Render la instancia puede estar suspendida y tardar
   ~30 s en responder; deje el timeout del módulo en 60 s o más.
3. Módulo de correo para `{{data.correo_destino}}`, con el contenido en **HTML** y el
   cuerpo `{{data.resumen_html}}`.
4. Opcional: un **Iterator** sobre `fallidos_para_reprogramar` y un correo por pedido a
   `{{correo}}`, para que el cliente coordine una nueva visita.

### Escenario 3: auditoría en Google Sheets

`GET /api/automatizacion/auditoria?desde_id=<n>&limite=<m>` entrega la auditoría por
lotes, en el orden de la hoja. Se autentica igual que el resumen diario
(`X-Automatizacion-Token`; **401** con otro token, **404** sin `AUTOMATIZACION_TOKEN`).

| Parámetro | Valor |
|---|---|
| `desde_id` | Último `id_auditoria` ya escrito en la hoja (0 la primera vez). Negativo: **400** |
| `limite` | Registros por lote: 200 por defecto, como máximo 500 |

```json
{
  "columnas": ["id_auditoria", "fecha_hora", "usuario", "rol", "origen", "accion", "entidad",
               "entidad_id", "resumen", "detalle", "resultado", "call_id"],
  "filas": [
    [1842, "2026-10-06T11:20:37-05:00", "Carlos Rueda", "Gestor logístico", "voz",
     "Anular pedido", "pedido", "", "Voy a anular el pedido PED-20261006-004 de Tienda La Esquina por: Duplicado.",
     "funcion: gestor/pedidos:anular\ndatos / operacion: anular\n…", "exito", "call_9f2c…"]
  ],
  "siguiente_desde_id": 1842,
  "hay_mas": false,
  "fecha_hora": "2026-10-06T12:00:02-05:00"
}
```

**Sin huecos.** Solo se entregan registros con al menos **60 segundos** de antigüedad, y el
recorrido es por `id`: se corta en el primer registro todavía reciente, aunque haya otros
posteriores ya viejos. En MySQL, dos transacciones simultáneas pueden confirmar el id 11
antes que el 10; con este corte el 11 no sale antes que el 10, y el cursor nunca salta un
registro. Un id que nunca llegó a existir (un *rollback*) no importa, porque el cursor es
"mayor que".

**Sin repetidos.** El cursor solo avanza si la hoja se escribió, y `id_auditoria` identifica
cada fila si un lote se reintenta.

Configuración del escenario:

1. Disparador **Schedule** cada hora (zona horaria *America/Bogota*).
2. **Data store → Get a record**: un almacén `cursor_auditoria` con la clave `auditoria` y
   el campo `desde_id` (número; 0 si no existe).
3. **HTTP → Make a request**: `GET`
   `https://<su-dominio>/api/automatizacion/auditoria?desde_id={{desde_id}}&limite=200`, con
   el encabezado `X-Automatizacion-Token` y *Parse response* activado; timeout de 60 s.
4. **Filtro**: continúa solo si `filas` no está vacío.
5. **Google Sheets → Bulk Add Rows (Advanced)**: hoja `Auditoria`, cuya primera fila tiene
   los nombres de `columnas`; *Rows* = `{{data.filas}}`. Es **una sola operación por lote**.
6. **Data store → Add/replace a record**: `auditoria` → `desde_id = {{data.siguiente_desde_id}}`.
   Va después de la escritura, así que solo se guarda si Bulk Add Rows salió bien; si
   falla, la siguiente ejecución vuelve a pedir el mismo lote.
7. Si `hay_mas` es `true`, quedan registros: la siguiente ejecución los trae. Para vaciar la
   cola de una vez, envuelva los pasos 2 a 6 en un **Repeater** que se detenga cuando
   `hay_mas` sea `false`.

Para detectar una fila repetida por un reintento (un lote que se escribió pero cuyo cursor
no alcanzó a guardarse), la columna A es `id_auditoria`: en la hoja, una columna auxiliar
`=COUNTIF(A:A; A2)>1` marca los duplicados, o un paso **Search Rows** por `id_auditoria`
antes de escribir los evita, a costa de una operación por fila.

### Demo con los datos sembrados

Defina `MAKE_WEBHOOK_URL`, `CORREO_OPERACIONES` y `SEMILLA_CORREO_CLIENTE` en `.env`
antes de sembrar. `seed.py` asigna ese correo a **Supermercado El Portal**, cuyo pedido
de hoy queda **asignado** en la ruta de `conductor1@sgds.com`. Los demás clientes
sembrados no tienen correo, así que no reciben avisos.

**`pedido_estado`**:

1. Inicie sesión como `conductor1@sgds.com` y abra la parada de Supermercado El Portal.
2. Márquela en camino, con el botón o por voz ("marca en camino la parada N") → aviso
   **EN_RUTA**.
3. Confirme la entrega en pantalla, o por voz con el nombre y el documento de quien
   recibe → aviso **ENTREGADO**.

Para ver un aviso **CANCELADO**, anule desde *Pedidos* (como `despachador@sgds.com`) un
pedido pendiente o asignado de Supermercado El Portal, indicando el motivo.

**`stock_bajo`**: la semilla deja el inventario así, sin ningún producto en negativo:

| SKU | Producto | Stock | Mínimo |
|---|---|---|---|
| SKU-1001 | Caja bebidas 12 und | 72 | 60 |
| SKU-1002 | Paquete snacks 24 und | 120 | 50 |
| SKU-1003 | Bolsa arroz 5 kg | 80 | 40 |
| SKU-1004 | Aceite vegetal 1 L | **30** | 45 |
| SKU-1005 | Detergente 2 kg | 90 | 30 |
| SKU-1006 | Papel higiénico 12 rollos | **18** | 25 |

Solo el aceite y el papel quedan bajo su mínimo, para que el tablero tenga alertas. Las
cifras finales son fijas: `seed.py` calcula el inventario inicial de cada producto a
partir de lo que consume el histórico (que depende de la fecha) y lo registra como una
entrada, de modo que la trazabilidad cuadra.

1. Inicie sesión como `despachador@sgds.com` y abra *Inventario → SKU-1001*, que está en
   72, sobre su mínimo de 60. Si ya bajó a 60 o menos, registre antes una **Entrada**.
2. Registre un **Ajuste por inventario físico** con cantidad **60** (o cualquier valor
   de 0 a 60) y un motivo → aviso **`stock_bajo`** con `negativo: false` y `pedido: null`.
3. Para repetirlo, vuelva a subirlo con una Entrada y repita el paso 2.

El formulario de movimientos manuales solo ofrece Entrada y Ajuste, y el ajuste no admite
valores negativos. Por eso el aviso con `negativo: true` solo lo dispara una
**entrega** que deja el stock por debajo de cero.

**Resumen diario**: con `AUTOMATIZACION_TOKEN` definido, ejecute el escenario 2 con *Run
once*, o pruébelo desde la terminal:

```bash
curl -H "X-Automatizacion-Token: $AUTOMATIZACION_TOKEN" \
     http://localhost:5001/api/automatizacion/resumen-diario
```

Para repetir la demo desde cero, vuelva a sembrar con `reset-db` y `seed.py`.

## Activación de Retell y Make

Hoy ambos están apagados: el código está completo y verificado, pero ninguna prueba
contacta Retell ni Make. Pasos para conectarlos, en orden (comandos de PowerShell desde
la raíz del proyecto):

1. **Base de datos.** Si la base ya tiene datos, aplique las migraciones pendientes; todas
   son idempotentes:

   ```powershell
   .venv\Scripts\flask.exe --app run migrar-asistente
   .venv\Scripts\flask.exe --app run migrar-configuracion
   .venv\Scripts\flask.exe --app run migrar-auditoria
   .venv\Scripts\flask.exe --app run migrar-pruebas-entrega
   ```

2. **URL pública.** Levante el servidor y exponga una URL `https://` con
   [ngrok](#ngrok) (o use la de Render). Sin ella Retell no llega a las funciones ni el
   navegador permite el micrófono fuera de `localhost`.
3. **Variables del `.env`** (y de las variables del despliegue):

   | Variable | Valor |
   |---|---|
   | `RETELL_API_KEY` | La API key de Retell **con el distintivo de webhook**: firma las funciones |
   | `URL_PUBLICA` | La URL `https://` del paso 2, sin barra final |
   | `RETELL_VOZ_ID` | Una voz en español del panel de Retell |
   | `RETELL_AGENTE_CONDUCTOR_ID`, `_GESTOR_ID`, `_ADMIN_ID`, `_CLIENTE_ID` | Vacías la primera vez; se llenan en el paso 4 |
   | `MAKE_WEBHOOK_URL` | El webhook del escenario de avisos (paso 5a) |
   | `MAKE_WEBHOOK_KEY` | La API key de los webhooks de Make (opcional, recomendada) |
   | `MAKE_WEBHOOK_CREDENCIALES_URL` | El webhook **aparte** del escenario de credenciales (paso 5b). Sin él, crear o restablecer cuentas por voz se rechaza |
   | `CORREO_OPERACIONES` | Destino de `stock_bajo`, `solicitud_contacto`, `accion_asistente` y el resumen diario |
   | `AUTOMATIZACION_TOKEN` | `python -c "import secrets; print(secrets.token_urlsafe(32))"`; lo usan los pasos 5c y 5d |

   Con `MAKE_WEBHOOK_URL` definido, correr `seed.py` a mano envía avisos reales (ver
   [Verificación](#verificación)). Las pruebas automáticas siguen sin contactar nada:
   vacían estas variables y bloquean la red.
4. **Agentes de Retell.** Cree o actualice los cuatro agentes y publíquelos
   ([detalle](#configurar-los-agentes-en-retell-sincronizar-asistentes)):

   ```powershell
   .venv\Scripts\flask.exe --app run sincronizar-asistentes
   ```

   Copie al `.env` los `agent_id` que imprime para los agentes nuevos y reinicie el
   servidor: el botón del micrófono aparece para cada rol con agente. Cada vez que cambie
   la URL de ngrok, vuelva a ejecutar el comando.
5. **Escenarios de Make:**
   - a. [Router de avisos](#escenario-1-webhook-con-router): un *Custom webhook* en
     `MAKE_WEBHOOK_URL` y un Router con una ruta por `tipo` (`pedido_estado`,
     `stock_bajo`, `solicitud_contacto`, `accion_asistente`).
   - b. [Credenciales](#escenario-de-credenciales-webhook-aparte): un escenario aparte con
     su webhook en `MAKE_WEBHOOK_CREDENCIALES_URL`, solo para `credencial_usuario`, con
     **"Data is confidential"** activado en *Scenario settings* para que Make no guarde
     las contraseñas.
   - c. [Resumen diario](#escenario-2-resumen-diario-programado): programado, con un
     `GET /api/automatizacion/resumen-diario` y el encabezado `X-Automatizacion-Token`.
   - d. [Auditoría a Google Sheets](#escenario-3-auditoría-en-google-sheets): programado,
     lee `GET /api/automatizacion/auditoria?desde_id=<n>&limite=200` por lotes y agrega
     las filas a la hoja `Auditoria`.
6. **Pruebas de voz recomendadas**, una llamada por rol con las cuentas de demostración:
   - *Conductor:* "¿cuál es mi ruta?"; iniciar la ruta; "la siguiente parada"; entregar
     una parada (Gaby debe pedir nombre y documento si faltan, y el stock debe bajar);
     registrar un fallo con un motivo fuera del catálogo (queda como "Otro"); "mi
     historial". En Make no debe llegar `accion_asistente` por estas acciones, sí
     `pedido_estado` si el cliente tiene correo.
   - *Gestor:* crear un pedido para un cliente no registrado (ofrece registrarlo y
     retoma el pedido); crear una ruta; ajustar el inventario (exige motivo); "las
     causas de fallo de la última semana"; atender una solicitud de contacto.
   - *Administrador:* crear un usuario (la contraseña llega solo por el escenario de
     credenciales; sin ese webhook se rechaza en el resumen); la bitácora filtrada por
     usuario o fecha; el resumen de rendimiento.
   - *Cliente:* el estado de un pedido; cancelar uno PENDIENTE; solicitar contacto
     (llega `solicitud_contacto` a operaciones). Preguntar por un pedido ajeno debe
     responderse como inexistente.
   - *En todas:* responder "no" al resumen no cambia nada; cada acción confirmada aparece
     en **Bitácora** con origen `voz` y su `call_id`.

## Verificación

Las pruebas corren solo en MySQL, en una base aparte: `logistica_pruebas` en el mismo
contenedor. Créela una vez con el usuario root del contenedor:

```powershell
docker exec sgds-mysql mysql -uroot -proot_clave -e "CREATE DATABASE IF NOT EXISTS logistica_pruebas CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci; GRANT ALL PRIVILEGES ON logistica_pruebas.* TO 'sgds'@'%';"
```

Y luego, desde la raíz del proyecto:

```powershell
.venv\Scripts\python.exe pruebas\ejecutar_todas.py              # todas, salida resumida
.venv\Scripts\python.exe pruebas\ejecutar_todas.py 09 17        # solo esas suites
.venv\Scripts\python.exe pruebas\ejecutar_todas.py --detalle    # salida completa
.venv\Scripts\python.exe pruebas\ejecutar_todas.py --externas   # incluye OSRM en vivo
.venv\Scripts\python.exe pruebas\prueba_05_entrega_inventario.py  # una suite sola
```

**1623 verificaciones en 18 suites contra MySQL 8.0, todas pasando, en unos dos
minutos.** La salida resumida muestra por suite las verificaciones, el tiempo y solo las
líneas `FALLA`; `--detalle` muestra todo.

**La base de pruebas se borra en cada suite, y nunca es la del `.env`.** Las pruebas usan
`mysql+pymysql://sgds:sgds_clave@127.0.0.1:3307/logistica_pruebas` (otra base con la
variable `PRUEBAS_DATABASE_URL`) y se niegan a correr si el nombre de la base no termina
en `_pruebas`; el `DATABASE_URL` del `.env` no se usa. El ejecutor siembra una sola vez y
cada suite restaura esos datos al arrancar, por lo que los resultados son reproducibles;
corrida sola, la suite siembra por su cuenta.

**Ninguna suite contacta servicios externos.** `pruebas/_preparar.py`, que cada suite
importa primero, vacía `MAKE_WEBHOOK_URL`, `MAKE_WEBHOOK_CREDENCIALES_URL` y
`RETELL_API_KEY` aunque el `.env` las defina, y limita la red a la propia máquina: Make,
Retell y OSRM se simulan o caen a su respaldo. La única excepción es la parte de OSRM en
vivo de la suite 3, que solo corre con `--externas` (suma 10 verificaciones).

| Suite | Cubre | Pruebas |
|---|---|---|
| `prueba_01_acceso.py` | RF1 · autenticación, roles, tablero, zona horaria, config. de producción, inventario sembrado | 46 |
| `prueba_02_pedidos_csv.py` | RF2 · alta manual, importación CSV, inventario, anulación | 64 |
| `prueba_03_ruteo.py` | RF3 · ordenamientos, respaldo offline (OSRM en vivo con `--externas`: 32) | 22 |
| `prueba_04_rutas_web.py` | RF3 · planificación, mapa, recálculo | 40 |
| `prueba_05_entrega_inventario.py` | RF4/RF5 · entrega, PoD, descuento de stock, concurrencia | 69 |
| `prueba_06_analitica_rendimiento.py` | RF6/RNF2 · indicadores, tiempos y zona horaria | 67 |
| `prueba_07_clientes_portal.py` | RF1/RF2 · normalización de clientes y portal | 73 |
| `prueba_08_administracion.py` | RF1 · administración de cuentas y clientes | 86 |
| `prueba_09_asistente.py` | RF4 · asistente de voz (firma, sesiones, aislamiento), avisos por Make, ruta finalizada del día y demo sembrada | 99 |
| `prueba_10_automatizaciones.py` | RF4/RF6 · avisos `pedido_estado` y `stock_bajo` (umbrales, CANCELADO y FALLIDO con motivo, API key), resumen diario (token, KPIs contra el tablero, fallidos, escape) | 79 |
| `prueba_11_asistente_roles.py` | RF1/RF4 · asistente por rol: agente y botón por rol, matriz de permisos, confirmación con estado, búsqueda para voz, funciones del gestor, del admin y del cliente, solicitudes de contacto, pedidos recientes y por fecha, migraciones | 215 |
| `prueba_12_sincronizacion_retell.py` | RF4 · configuración de los agentes (herramientas, prompts, documento) y `sincronizar-asistentes` contra un Retell simulado con el versionado real (agente y LLM en la misma versión, el 400 si no coinciden, segunda sincronización, borrador pendiente, `--rol`), timeout de 60 s con reintento, Talk While Waiting | 94 |
| `prueba_13_configuracion.py` | RF4/RF6 · configuración del negocio: catálogo de motivos de fallo con "Otro" y texto obligatorio, centro de distribución editable, uso en pantalla, voz y analítica, migración `migrar-configuracion` (en MySQL: clave ajena y coordenadas `FLOAT` → `DOUBLE`) | 59 |
| `prueba_14_reglas_pantalla.py` | RF1/RF2/RF3 · reglas de pantalla en servicios: NIT fijo, sedes, SKU, vehículos, edición de pedidos y bitácora de rutas | 62 |
| `prueba_15_auditoria.py` | RF1/RF4 · auditoría de solo agregar, pantalla *Bitácora*, quién originó cada evento de Make, `accion_asistente` (con `id_auditoria` y `fecha_hora`) y `credencial_usuario` por su webhook, auditoría de pantalla sin correo, endpoint incremental para Sheets | 87 |
| `prueba_16_cobertura_voz.py` | RF4 · cobertura por voz de cada ruta POST de la pantalla, exclusiones aprobadas, diálogo de formulario, cobertura cerrada | 289 |
| `prueba_17_voz_fase3.py` | RF1/RF4 · voz: clientes, sedes, productos, vehículos, rutas y edición de pedidos (gestor y admin); usuarios con credenciales por su webhook, configuración y bitácora (admin); resúmenes, rechazos y auditoría | 94 |
| `prueba_18_voz_fase4.py` | RF1/RF4/RF5 · voz de la fase 4: el conductor inicia la ruta, entrega con nombre y documento y consulta su historial (auditoría sin correo); analítica y `atender-solicitud` del gestor; bitácora del admin; migración m007 y un pedido que falla, se reintenta y se entrega conservando cada prueba | 78 |

La del asistente firma las peticiones con el propio SDK de Retell para probar la
verificación real; la de automatizaciones registra cada aviso a Make simulado con sus
encabezados, y la de sincronización usa un Retell simulado que valida cada llamada contra
la firma real del SDK. La de rutas en pantalla usa un OSRM simulado.

**`seed.py` corrido a mano sí avisa a Make.** Fuera de las pruebas, sembrar con
`MAKE_WEBHOOK_URL` definido en el `.env` envía los avisos que generan los datos de
demostración al escenario real. Para sembrar sin avisos, comente esa línea del `.env`
antes de correr `seed.py` (en PowerShell, `$env:MAKE_WEBHOOK_URL = ""` no sirve: borra la
variable y el `.env` vuelve a aportarla).

## Medición del RNF2

Un middleware cronometra cada petición, la persiste en `mediciones_rendimiento` y
publica el valor en la cabecera `X-Tiempo-Respuesta-ms`. El panel **Rendimiento**
(solo administrador) muestra mediana, percentil 95 y máximo por transacción crítica,
contrastados con el límite de 5 segundos.

La escritura de telemetría ocurre en **su propia transacción**, no en la sesión de la
petición: un `commit` allí confirmaría cambios que una vista pudo dejar pendientes
deliberadamente. Si la telemetría falla, la petición continúa sin verse afectada.

Medición local con la base sembrada (217 pedidos históricos): mediana ~1 ms,
percentil 95 ~7 ms, máximo ~11 ms. **Tres órdenes de magnitud por debajo del límite.**
La medición es del lado del servidor y no incluye la latencia de la red móvil, que el
propio RNF2 contempla aparte al condicionar el límite "a la conexión a internet móvil".

## Analítica de la operación

El panel **Analítica** amplía el RF6 con los indicadores que el objetivo específico pide
para "facilitar la toma de decisiones estratégicas":

- **Tiempo promedio de entrega** — minutos entre *En ruta* y *Entregado*, calculado
  desde la bitácora. Mide directamente el problema de "tiempos de entrega ineficientes"
  del objetivo general.
- **Cumplimiento de ventana horaria** — porcentaje de entregas dentro del horario pactado.
- **Entregas por día** — columna apilada de exitosas y fallidas.
- **Causas de entrega fallida** — ranking que permite atacar la causa dominante, no solo
  registrarla.
- **Productividad por conductor** — entregas cerradas y tasa de éxito.

### Nota sobre los colores de las gráficas

La paleta se validó con un verificador de accesibilidad cromática contra la superficie
real de las tarjetas. **El par verde/rojo fue rechazado**: su separación bajo
deuteranopía es ΔE 4.1, muy por debajo del mínimo de 6 — un lector con daltonismo rojo-verde
(cerca del 8 % de los hombres) no distinguiría las series. Se usa azul `#2a78d6` y
naranja `#eb6834`, que miden ΔE 24.7 bajo simulación de protanopía. El estado además se
identifica por leyenda y etiqueta, nunca por color solo, y cada gráfica ofrece una vista
en tabla.

## MySQL y MySQL Workbench (fase piloto)

**La migración está verificada:** las 17 tablas se crean correctamente y las 1623
verificaciones (incluidas las propias de MySQL: claves ajenas y precisión de las
coordenadas) pasan íntegras contra MySQL 8.0.46.

`docker-compose.yml` no necesita cambios al evolucionar el esquema: solo provisiona
el servidor MySQL. Las tablas las crea el ORM, así que `init-db` recoge los modelos
nuevos por su cuenta.

### Levantar la base (base vacía)

```bash
docker compose up -d                    # MySQL 8 en el puerto 3307
.venv/bin/flask --app run init-db       # Crea el esquema
.venv/bin/python seed.py                # Carga los datos de demostración
```

En `.env`, la línea que selecciona el motor:

```
DATABASE_URL=mysql+pymysql://sgds:sgds_clave@127.0.0.1:3307/logistica
```

Comentarla devuelve el proyecto a SQLite sin ningún otro cambio.

### Base que ya tiene operación cargada

`init-db` agrega tablas nuevas pero **no columnas nuevas en tablas existentes**, y la
normalización de clientes agrega dos a `pedidos`. Sobre una base con datos:

```bash
.venv/bin/flask --app run migrar-clientes
```

Crea `clientes` y `direcciones_cliente`, agrega `pedidos.cliente_id` y
`pedidos.direccion_id`, **declara el índice y las claves ajenas** que un
`ALTER TABLE ADD COLUMN` no genera, y reconstruye los clientes agrupando el histórico
por nombre normalizado. Es idempotente.

El paso de las claves ajenas no es cosmético: **MySQL Workbench dibuja el diagrama EER
a partir de las claves ajenas declaradas**, así que sin él las tablas de clientes
aparecerían sueltas, sin las líneas que las unen a `pedidos`, y esas dos columnas
quedarían sin integridad referencial. Verificado contra una base MySQL con datos: el
esquema resultante es idéntico al que produce `init-db` sobre una base vacía.

Las funciones posteriores tienen su propia migración, también idempotente; en una base
con datos ejecútelas en este orden:

```bash
.venv/bin/flask --app run migrar-asistente      # sesiones del asistente y solicitudes de contacto
.venv/bin/flask --app run migrar-configuracion  # motivos de fallo, centro de distribución
.venv/bin/flask --app run migrar-auditoria      # tabla de auditoría (bitácora)
.venv/bin/flask --app run migrar-pruebas-entrega # pruebas de entrega de solo agregar (m007)
```

`migrar-configuracion` además pasa a `DOUBLE` las coordenadas del centro de distribución
si la tabla se creó con `FLOAT`: en MySQL, `FLOAT` es de precisión simple y guardaba
4.6482837 como 4.64828. Los valores ya redondeados no se recuperan; si necesita la
precisión completa, vuelva a guardar el centro en *Configuración*.

### Conectar MySQL Workbench

| Campo | Valor |
|---|---|
| Connection Name | `SGDS Local` |
| Hostname | `127.0.0.1` |
| Port | `3307` |
| Username | `sgds` |
| Password | `sgds_clave` |
| Default Schema | `logistica` |

El usuario `root` (contraseña `root_clave`) queda disponible para tareas
administrativas. Ambas credenciales se definen en `docker-compose.yml`.

### Diagrama EER

**Database → Reverse Engineer** sobre el esquema `logistica` produce las 12 tablas con
sus relaciones. Las que introduce la normalización de clientes:

| Relación | Cardinalidad |
|---|---|
| `clientes.usuario_id` → `usuarios.id` | 1 a 0..1 (única y nullable) |
| `direcciones_cliente.cliente_id` → `clientes.id` | 1 a N |
| `pedidos.cliente_id` → `clientes.id` | 1 a N |
| `pedidos.direccion_id` → `direcciones_cliente.id` | 1 a N |

### Administrar el contenedor

```bash
docker compose ps        # Estado
docker compose stop      # Detener (conserva los datos)
docker compose start     # Reanudar
docker compose down -v   # Eliminar contenedor Y datos
```

Los datos viven en un volumen de Docker, así que sobreviven a reinicios del
equipo y a `docker compose down` sin la bandera `-v`.

### Un ajuste que exigió MySQL

La polilínea de una ruta se guardaba en `TEXT`, que MySQL limita a 65 535 bytes.
Una ruta de solo 5 paradas ya ocupa 33 714 bytes; una de 12 paradas excedería el
límite y MySQL truncaría el trazado. SQLite no tiene ese límite, así que el
problema solo habría aparecido en el piloto. La columna declara la variante
`MEDIUMTEXT` (16 MB) para MySQL, transparente para SQLite. Verificado en la base
real: `CHARACTER_MAXIMUM_LENGTH = 16777215`.

### Rendimiento comparado

Los tiempos de respuesta suben al pasar de un archivo local a TCP, y siguen tres
órdenes de magnitud por debajo del límite del RNF2:

| Pantalla | SQLite | MySQL |
|---|---|---|
| Tablero | ~1 ms | ~17 ms |
| Analítica | ~11 ms | ~24 ms |
| Rutas | ~7 ms | ~53 ms |

## Despliegue

`Procfile` y `render.yaml` están listos para Render o Railway, con gunicorn como servidor
WSGI y `/salud` como health check. Recuerde que el plan gratuito de Render suspende la
instancia tras 15 minutos sin tráfico y el siguiente acceso tarda ~30 segundos.
