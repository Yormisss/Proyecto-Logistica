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
│   ├── controllers/       CONTROLADOR — auth, admin, usuarios, pedidos, inventario, conductor, cliente
│   ├── services/          Lógica de negocio — despacho, ruteo, analítica, importador
│   ├── views/             VISTA    — plantillas Jinja2
│   └── static/            CSS y JS
├── migraciones/           Cambios de esquema aplicables sobre una base con datos
├── pruebas/               393 verificaciones automatizadas en 8 suites
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
                  ▲            │
                  │            └────► FALLIDO       (NO descuenta inventario)
                  │                      │
                  └──────────────────────┘          (reintento)
```

Las transiciones se validan en el modelo (`EstadoPedido.TRANSICIONES`). Un pedido
entregado es terminal: no admite reversión, lo que impide descontar el inventario dos
veces por un error de operación.

### Garantías implementadas

| Situación | Comportamiento |
|---|---|
| El conductor pulsa "Entregar" dos veces (conectividad intermitente) | La bandera `inventario_descontado` hace la operación idempotente: el stock se afecta una sola vez |
| Entrega fallida | Se registra el motivo y la PoD, pero el inventario **no** se toca |
| Reintento tras un fallo | Vuelve a `EN_RUTA`; si luego se entrega, ahí sí descuenta |
| Stock insuficiente al entregar | La entrega **no se bloquea** (la mercancía ya salió físicamente): se descuenta, el stock queda negativo y el sistema alerta del descuadre para que el administrador lo concilie |
| Última parada cerrada | La ruta pasa automáticamente a `FINALIZADA` y registra la hora |
| Un conductor intenta operar la parada de otro | 403 |
| La sesión caduca con la pantalla abierta | Página en español explicando que expiró, en lugar del error crudo de Flask |

Cada cambio de estado deja un `EventoPedido` con usuario, hora y coordenadas, y cada
descuento un `MovimientoInventario` que cita el pedido de origen: trazabilidad completa
en ambos sentidos.

### Geolocalización desde el teléfono del conductor

La limitación de "dependencia de hardware de terceros" (numeral 1.4) implica que no hay
dispositivos IoT ni GPS dedicados. La interfaz usa la API de geolocalización del
navegador para adjuntar las coordenadas a cada actualización de estado. **Si el conductor
niega el permiso o no hay señal, la acción se envía igual**: registrar el estado es lo
crítico en terreno; la ubicación es complementaria.

## Verificación

```bash
.venv/bin/python pruebas/ejecutar_todas.py
```

**393 verificaciones en 8 suites**, todas pasando. Cada suite reinicia y resiembra la
base, por lo que los resultados son reproducibles.

| Suite | Cubre | Pruebas |
|---|---|---|
| `prueba_01_acceso.py` | RF1 · autenticación, roles, tablero | 15 |
| `prueba_02_pedidos_csv.py` | RF2 · alta manual, importación CSV, inventario | 50 |
| `prueba_03_ruteo.py` | RF3 · OSRM, ordenamientos, respaldo offline | 29 |
| `prueba_04_rutas_web.py` | RF3 · planificación, mapa, recálculo | 40 |
| `prueba_05_entrega_inventario.py` | RF4/RF5 · entrega, PoD, descuento de stock | 64 |
| `prueba_06_analitica_rendimiento.py` | RF6/RNF2 · indicadores y tiempos | 57 |
| `prueba_07_clientes_portal.py` | RF1/RF2 · normalización de clientes y portal | 64 |
| `prueba_08_administracion.py` | RF1 · administración de cuentas y clientes | 74 |

La suite de ruteo requiere internet para probar OSRM; sin conexión verifica igualmente
el algoritmo local de respaldo.

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
**La migración está verificada:** las 12 tablas se crean correctamente y las 393
pruebas pasan íntegras contra MySQL 8.0.46.

`docker-compose.yml` no necesita cambios al evolucionar el esquema: solo provisiona
el servidor MySQL. Las tablas las crea el ORM, así que `init-db` recoge los modelos
nuevos por su cuenta.

### Levantar la base (base vacía)

```bash
docker compose up -d                    # MySQL 8 en el puerto 3306
.venv/bin/flask --app run init-db       # Crea el esquema
.venv/bin/python seed.py                # Carga los datos de demostración
```

En `.env`, la línea que selecciona el motor:

```
DATABASE_URL=mysql+pymysql://sgds:sgds_clave@127.0.0.1:3306/logistica
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
| Port | `3306` |
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
