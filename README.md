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
├── pruebas/               929 verificaciones automatizadas en 12 suites
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
| Se cambia el rol de un conductor con rutas sin cerrar | Se permite, pero avisa cuántas hay que reasignar |
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

| Rol | Consultas | Acciones (con confirmación) |
|---|---|---|
| **Conductor** | Su ruta de hoy (o el cierre si ya la terminó), la siguiente parada, el detalle de una parada | Marcar una parada en camino o reintentarla, registrar una entrega fallida con su motivo |
| **Gestor logístico** | Resumen del día (KPIs), pendientes sin ruta, avance de las rutas por conductor, un pedido (estado, ruta, conductor, bitácora), stock de un producto, productos bajo mínimo, fallidos de hoy, solicitudes de contacto pendientes | Crear un pedido (cliente y sede registrados, productos existentes, fecha desde hoy), anular un pedido, reintentar un fallido de una ruta de hoy, registrar una entrada de mercancía, cambiar la prioridad, agregar un pedido pendiente a la ruta de hoy de un conductor (recalcula la secuencia) |
| **Administrador** | Todo lo del gestor, más tiempo promedio de entrega, cumplimiento de ventana, productividad por conductor, causas de fallo (7, 14 o 30 días) y el resumen del panel de rendimiento | Todo lo del gestor, más ajustar el inventario de un producto a un valor exacto con motivo |
| **Cliente** | Sus pedidos en curso, el estado e historial de un pedido (con la traducción del portal), su ventana de entrega, el motivo de un fallo, sus sedes | Cancelar un pedido propio que sigue PENDIENTE, solicitar que el gestor lo contacte (una solicitud pendiente a la vez) |

Las acciones usan los mismos servicios que la pantalla (`despacho`, `pedidos`,
`inventario`, `planificacion`, `solicitudes`), así que aplican las mismas reglas,
disparan los mismos avisos de Make y dejan en la bitácora la nota *"Registrado por el
asistente de voz"*. La lista completa de funciones, con sus parámetros, está en
[docs/configuracion_retell.md](docs/configuracion_retell.md).

### Lo que queda fuera de la voz, y por qué

| Rol | Excluido | Por qué |
|---|---|---|
| Conductor | Confirmar una entrega | Exige la prueba de entrega (PoD) en pantalla y descuenta inventario (RF5) |
| Gestor | Importar CSV, crear o eliminar rutas | Necesitan revisar una vista previa o un mapa; una ruta finalizada tampoco se reabre al agregarle paradas: se crea una nueva en pantalla |
| Gestor | Ajustes de inventario | Fijan el stock a un valor absoluto: solo el administrador puede hacerlo |
| Gestor y admin | Crear clientes o sedes | Por voz solo se usan los registrados, para no duplicarlos con otra escritura |
| Gestor y admin | Marcar atendida una solicitud de contacto | Se hace en la pantalla *Solicitudes*, después de contactar al cliente |
| Admin | Usuarios, roles, contraseñas y activación de cuentas | Afectan el acceso al sistema; no hay ninguna función de voz para eso y el agente indica que se hace en *Usuarios* |
| Cliente | Crear pedidos | Depende de la fase del portal pendiente |
| Cliente | Cancelar un pedido ya programado | Desde ASIGNADO ya hay una ruta planificada: el asistente ofrece solicitar contacto con el gestor |
| Cliente | Rutas, conductores, stock, datos de otros clientes | El mismo aislamiento del portal: un pedido ajeno se responde igual que uno inexistente |

### Cómo funciona

1. El navegador pide permiso para el micrófono. Si el usuario lo niega, se le explica
   cómo habilitarlo y no se crea ninguna llamada.
2. `POST /asistente/llamada` (sesión iniciada y token CSRF) elige el agente según el rol
   (403 si el rol no tiene), crea la llamada con `retell-sdk` en `/v3/create-web-call`
   pasando como variables dinámicas `nombre_usuario` y `fecha_hoy` (hora de Bogotá), y
   guarda `call_id → usuario` y el rol en `sesiones_asistente` con una vigencia de
   **10 minutos**.
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
consultan por voz, y las marcan como atendidas en pantalla.

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
   español latino (`es-419`), **duración máxima de 5 minutos** (la sesión del servidor dura
   10, así una llamada nunca sobrevive a su sesión) y **fin tras 20 segundos de
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

## Automatizaciones con Make

La aplicación se integra con [Make](https://www.make.com) en dos sentidos, ambos
opcionales: **avisa** a un webhook de Make cuando ocurre algo (escenario 1) y **expone un
resumen diario** que un escenario programado consulta y envía por correo (escenario 2).
Sin las variables correspondientes, la aplicación funciona igual y no sale nada.

### Variables

| Variable | Para qué | Si está vacía |
|---|---|---|
| `MAKE_WEBHOOK_URL` | URL del *Custom webhook* que recibe los avisos | No se envía ningún aviso |
| `MAKE_WEBHOOK_KEY` | API key del webhook; viaja en el encabezado `x-make-apikey` | Los avisos salen sin el encabezado |
| `CORREO_OPERACIONES` | Destino de los avisos internos (`stock_bajo`, `solicitud_contacto`) y del resumen diario | No se envían avisos internos; el resumen responde `correo_destino: null` |
| `AUTOMATIZACION_TOKEN` | Token con el que Make consulta el resumen diario | El endpoint del resumen responde **404** |

Genere el token con `python -c "import secrets; print(secrets.token_urlsafe(32))"`.

### Eventos salientes

Todo aviso es un `POST` JSON a `MAKE_WEBHOOK_URL` con un campo **`tipo`**, y todos usan
el mismo mecanismo:

- Solo sale **después del commit**: un cambio revertido no genera correo.
- Va en segundo plano, con un timeout de 3 s de conexión y 5 s de respuesta. **Si Make
  falla o tarda, la operación no se bloquea ni se revierte**; el fallo queda solo en el log.

| `tipo` | Cuándo | Destinatario | Campos |
|---|---|---|---|
| `pedido_estado` | Un pedido pasa a **EN_RUTA, ENTREGADO o FALLIDO** desde cualquier origen (vista móvil, "Iniciar ruta" o asistente de voz), o se **anula** (CANCELADO) | El cliente, en `correo`. Si no tiene correo, no se envía | `codigo`, `estado`, `cliente`, `correo`, `direccion`, `ventana`, `hora`; en FALLIDO (motivo de la prueba de entrega) y en CANCELADO (motivo de la anulación), además `motivo` |
| `stock_bajo` | Una entrega o un movimiento manual **cruza** un umbral del stock de un producto activo (ver abajo) | Operaciones, en `correo_destino` (`CORREO_OPERACIONES`) | `sku`, `producto`, `stock_actual`, `stock_minimo`, `negativo`, `pedido` (código del pedido entregado, o `null` si fue manual), `correo_destino`, `hora` |
| `solicitud_contacto` | Un cliente pide por el asistente de voz que el gestor lo contacte (una pendiente a la vez por cliente) | Operaciones, en `correo_destino` (`CORREO_OPERACIONES`) | `cliente`, `correo`, `telefono`, `motivo`, `hora`, `correo_destino` |

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
  "motivo": "El cliente cancelo la compra"
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
  "hora": "2026-10-06 10:40"
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
  "correo_destino": "operaciones@ejemplo.com"
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
     "motivo": "Cliente ausente"}
  ],
  "resumen_html": "<div style=\"font-family:Arial…\">…</div>"
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

### Demo con los datos sembrados

Defina `MAKE_WEBHOOK_URL`, `CORREO_OPERACIONES` y `SEMILLA_CORREO_CLIENTE` en `.env`
antes de sembrar. `seed.py` asigna ese correo a **Supermercado El Portal**, cuyo pedido
de hoy queda **asignado** en la ruta de `conductor1@sgds.com`. Los demás clientes
sembrados no tienen correo, así que no reciben avisos.

**`pedido_estado`**:

1. Inicie sesión como `conductor1@sgds.com` y abra la parada de Supermercado El Portal.
2. Márquela en camino, con el botón o por voz ("marca en camino la parada N") → aviso
   **EN_RUTA**.
3. Confirme la entrega en pantalla → aviso **ENTREGADO**.

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
   de 1 a 60) → aviso **`stock_bajo`** con `negativo: false` y `pedido: null`.
3. Para repetirlo, vuelva a subirlo con una Entrada y repita el paso 2.

El formulario de movimientos manuales solo ofrece Entrada y Ajuste, y el ajuste no admite
valores menores que 1. Por eso el aviso con `negativo: true` solo lo dispara una
**entrega** que deja el stock por debajo de cero.

**Resumen diario**: con `AUTOMATIZACION_TOKEN` definido, ejecute el escenario 2 con *Run
once*, o pruébelo desde la terminal:

```bash
curl -H "X-Automatizacion-Token: $AUTOMATIZACION_TOKEN" \
     http://localhost:5001/api/automatizacion/resumen-diario
```

Para repetir la demo desde cero, vuelva a sembrar con `reset-db` y `seed.py`.

## Verificación

```bash
.venv/bin/python pruebas/ejecutar_todas.py
```

**929 verificaciones en 12 suites**, todas pasando. Cada suite reinicia y resiembra la
base, por lo que los resultados son reproducibles.

**Cada suite borra la base configurada.** Si su `.env` apunta a una base con datos que
quiere conservar, pase otra base solo a ese comando (la variable de entorno tiene
prioridad sobre el `.env`):

```bash
# SQLite temporal
DATABASE_URL=sqlite:////tmp/sgds_pruebas.db .venv/bin/python pruebas/ejecutar_todas.py
# MySQL, en una base aparte (créela una vez con el usuario root del contenedor)
docker exec sgds-mysql mysql -uroot -proot_clave -e "CREATE DATABASE IF NOT EXISTS logistica_pruebas CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci; GRANT ALL PRIVILEGES ON logistica_pruebas.* TO 'sgds'@'%';"
DATABASE_URL=mysql+pymysql://sgds:sgds_clave@127.0.0.1:3307/logistica_pruebas .venv/bin/python pruebas/ejecutar_todas.py
```

| Suite | Cubre | Pruebas |
|---|---|---|
| `prueba_01_acceso.py` | RF1 · autenticación, roles, tablero, zona horaria, config. de producción, inventario sembrado | 46 |
| `prueba_02_pedidos_csv.py` | RF2 · alta manual, importación CSV, inventario, anulación | 64 |
| `prueba_03_ruteo.py` | RF3 · OSRM, ordenamientos, respaldo offline | 31 |
| `prueba_04_rutas_web.py` | RF3 · planificación, mapa, recálculo | 40 |
| `prueba_05_entrega_inventario.py` | RF4/RF5 · entrega, PoD, descuento de stock, concurrencia | 69 |
| `prueba_06_analitica_rendimiento.py` | RF6/RNF2 · indicadores, tiempos y zona horaria | 67 |
| `prueba_07_clientes_portal.py` | RF1/RF2 · normalización de clientes y portal | 68 |
| `prueba_08_administracion.py` | RF1 · administración de cuentas y clientes | 79 |
| `prueba_09_asistente.py` | RF4 · asistente de voz (firma, sesiones, aislamiento), avisos por Make, ruta finalizada del día y demo sembrada | 97 |
| `prueba_10_automatizaciones.py` | RF4/RF6 · avisos `pedido_estado` y `stock_bajo` (umbrales, CANCELADO y FALLIDO con motivo, API key), resumen diario (token, KPIs contra el tablero, fallidos, escape) | 74 |
| `prueba_11_asistente_roles.py` | RF1/RF4 · asistente por rol: agente y botón por rol, matriz de permisos, confirmación con estado, búsqueda para voz, funciones del gestor, del admin y del cliente, solicitudes de contacto, pedidos recientes y por fecha, migraciones | 211 |
| `prueba_12_sincronizacion_retell.py` | RF4 · configuración de los agentes (herramientas, prompts, documento) y `sincronizar-asistentes` contra un Retell simulado con el versionado real (agente y LLM en la misma versión, el 400 si no coinciden, segunda sincronización, borrador pendiente, `--rol`), timeout de 60 s con reintento, Talk While Waiting | 83 |

La suite de ruteo requiere internet para probar OSRM; sin conexión verifica igualmente
el algoritmo local de respaldo. La del asistente corre sin internet: simula Retell y
Make, y firma las peticiones con el propio SDK de Retell para probar la verificación real.
La de automatizaciones también corre sin internet: simula Make y registra cada aviso con
sus encabezados. La del asistente por rol simula además el ruteo sin red, y la de
sincronización usa un Retell simulado que valida cada llamada contra la firma real del SDK.

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

El proyecto corre indistintamente sobre SQLite (desarrollo) o MySQL (piloto).
**La migración está verificada:** las 14 tablas se crean correctamente y las 929
pruebas (934 contra MySQL, que suma las verificaciones de claves ajenas propias
de ese motor) pasan íntegras contra MySQL 8.0.46.

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
