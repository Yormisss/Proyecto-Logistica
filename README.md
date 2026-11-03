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
│   └── static/            CSS, JS, fuentes e imágenes (logo y favicon en img/)
├── migraciones/           Cambios de esquema aplicables sobre una base con datos
├── docs/                  Configuración generada de los agentes de Retell
├── pruebas/               2480 verificaciones automatizadas en 28 suites (MySQL)
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
.venv/bin/flask --app run migrar-contrasenas  # Columnas de gestión de contraseñas y tabla de intentos de acceso
.venv/bin/flask --app run migrar-seguridad    # Sesión única, inactividad, errores (m010), cifrado (m011), roles con permisos (m012), llamadas de voz atadas a la sesión web (m013), permiso de enviar el resumen (m014) y correo de avisos (m015)
.venv/bin/flask --app run enviar-resumen   # Enviar el resumen del día a Make (para programarlo)
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

Para recibir sus avisos en correos reales, defina en el `.env` `SEMILLA_CORREO_ADMIN`,
`SEMILLA_CORREO_GESTOR` y `SEMILLA_CORREO_CONDUCTOR` (a `conductor1`): `seed.py` los pone
como su correo de avisos. Sin ellas, sus avisos van al correo de inicio de sesión
(`@sgds.com`, que no existe).

Las cuentas sembradas **no** piden cambiar la contraseña al entrar. Las que se crean o
restablecen después (en pantalla o por voz) sí, porque su contraseña es temporal.

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

### Roles y permisos

Cada pantalla exige un **permiso** (`requiere_permiso` en `app/controllers/seguridad.py`), y
cada cuenta tiene los suyos según sus roles. El catálogo está en `app/permisos.py`: una
sección con dos niveles, *ver* y *gestionar* (por ejemplo *Ver pedidos* y *Gestionar
pedidos*). Una pantalla que muestra y guarda en la misma ruta, como el detalle de un
producto con su formulario de movimiento, exige *ver* para abrirla y *gestionar* para
guardar.

Los permisos de una cuenta (`app/services/permisos.py`) se calculan así:

1. Los de su **rol principal**, que es el tipo de cuenta y se elige en *Editar*.
2. Más los de sus **roles adicionales**.
3. Con sus **ajustes propios**, permiso por permiso: *según sus roles*, *conceder* (lo da
   aunque ningún rol lo dé) o *denegar* (lo quita aunque un rol lo dé).

| Pantalla | Qué se hace |
|---|---|
| **Administración > Roles** | Crear roles con sus permisos, editar el reparto de *Gestor logístico* y *Conductor*, eliminar los creados. *Administrador* (todos los permisos) y *Cliente* (solo su portal) no se editan |
| **Usuarios > Permisos** (en cada cuenta) | Sumar roles adicionales y ajustar cada permiso. Muestra de qué roles viene cada uno y si la cuenta lo tiene |

- **Enviar el resumen del día** (`resumen.enviar`) es un permiso propio: lo tienen el
  administrador y el gestor, y se puede repartir o quitar como cualquier otro. En una base
  con datos, `migrar-seguridad` (m014) se lo agrega al rol del gestor.
- **Una base nueva tiene el reparto de siempre.** Los cuatro roles del sistema quedan con
  los permisos que tenían cuando eran fijos: nadie gana ni pierde acceso al migrar.
- **El menú y los botones siguen a los permisos.** Quien solo puede *ver* pedidos no ve
  *Nuevo pedido* ni *Importar CSV*. Si la cuenta no puede abrir la página de su rol (por
  ejemplo, se le negó el tablero), entra a la primera que sí pueda.
- **La voz también los respeta.** Cada función del asistente exige el mismo permiso que
  su pantalla (`PERMISOS_VOZ`), con el mismo `Usuario.puede`: quitarle *Gestionar
  inventario* al gestor, a su rol o a su cuenta, también se lo quita por voz, y la suite 26
  verifica que cada operación de voz pida exactamente el permiso de su ruta. Los permisos
  ganados con roles adicionales no amplían el asistente, que sigue siendo uno por tipo de
  cuenta.
- **Y se administran por voz.** El administrador puede consultar, crear, editar y
  eliminar roles, agregarles o quitarles un permiso, y asignar o quitar roles adicionales
  y ajustes de una cuenta, con las mismas reglas de esta sección (`admin/roles` y
  `admin/permisos`).
- **Contra la escalada de privilegios.** Gestionar usuarios, contraseñas y roles es **solo
  de los administradores**: ningún rol ni ajuste lo da. Quien pudiera hacerlo podría darse
  a sí mismo cualquier otro permiso o el rol de administrador. Tampoco se reparten la ruta
  del conductor ni el portal del cliente, que dependen del tipo de cuenta. Una cuenta de
  cliente no lleva roles adicionales ni ajustes, y al administrador no se le quita nada.
  Las reglas se validan en el servidor, también ante un POST fabricado.
- Los cambios aplican desde la siguiente acción de cada cuenta, sin cerrar su sesión, y
  quedan en la bitácora (*Crear rol*, *Editar rol*, *Eliminar rol*, *Editar permisos de
  usuario*).

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

Al crear una cuenta, si el administrador deja el campo en blanco, el sistema genera una
contraseña temporal con `secrets` y **la muestra una sola vez** para que se entregue por un
canal aparte. *Restablecer clave* hace lo mismo sobre una cuenta existente.

Toda contraseña que se escribe (alta, *Asignar*, *Cambiar la mía* y *Mi perfil*) debe
tener **mínimo 8 caracteres, con al menos una mayúscula, una minúscula, un número y un
símbolo**. Así no pasan secuencias como `123456789101112131415` ni palabras sueltas, y el
mensaje dice qué le falta. La regla es una sola (`error_politica` en
`app/services/usuarios.py`), y las contraseñas generadas también la cumplen.

Todo campo de contraseña lleva un botón con un ojo: **la contraseña se ve solo mientras se
mantiene presionado** (ratón, dedo, o Espacio o Enter con el teclado). Vuelve a ocultarse al
soltarlo, al sacar el puntero del botón, al cambiar de ventana y al enviar el formulario.
Aun visible, el campo no deja copiar ni cortar su contenido. El ojo propio de Edge, que la
deja a la vista con un clic, queda oculto. Ver `app/static/js/contrasena.js`.

El apartado **Contraseñas** (menú del administrador, `/admin/usuarios/contrasenas`) muestra
el estado de la contraseña de todas las cuentas. Como solo se guarda el hash (RNF5),
**nadie puede ver una contraseña**, tampoco el administrador.

Quién puede cambiar cada contraseña:

| Cuenta | Quién la cambia |
|---|---|
| Gestor logístico, conductor | El administrador (desde el apartado) o la propia persona (desde *Mi perfil*) |
| El propio administrador | Él mismo, con la actual, desde el apartado o desde *Mi perfil* |
| Cliente | **Solo el cliente**, desde *Mi perfil* |
| Otro administrador | Solo ese administrador |

El administrador entrega la contraseña inicial al crear una cuenta, pero la de un cliente o
de otro administrador **siempre es temporal**: deben cambiarla al entrar, así que solo ellos
conocen la definitiva. La regla se valida en el servidor (`_rechazo_contrasena` en
`app/controllers/usuarios.py`), no solo ocultando botones. Un POST fabricado contra la
cuenta de un cliente se rechaza. La regla depende del rol actual de la cuenta, y cambiar el
rol sigue siendo facultad del administrador.

El apartado muestra:

| Columna | Qué indica |
|---|---|
| Último cambio | Fecha del último cambio. Las cuentas anteriores a `migrar-contrasenas` aparecen *Sin registro* |
| Estado | *Pendiente de cambio* (temporal o cambio exigido) o *Vigente* |
| Protección | *PBKDF2-SHA256* si el hash sigue `METODO_HASH`; *Revisar* si usa otro algoritmo |

Acciones sobre gestores logísticos y conductores:

| Acción | Efecto |
|---|---|
| **Asignar** | Fija una contraseña concreta (con confirmación). Por defecto exige cambiarla al entrar |
| **Generar temporal** | Reemplaza la contraseña por una aleatoria que se muestra una sola vez |
| **Exigir cambio** | No toca el hash: la persona entra con la contraseña que ya conoce y el sistema le pide una nueva. Se puede retirar |
| **Exigir cambio a las seleccionadas** | Lo mismo en bloque, por ejemplo ante una sospecha de filtración. Omite clientes y administradores aunque el POST los incluya |
| **Cambiar la mía** | Solo en la fila propia: pide la actual, la nueva y su confirmación |

Una cuenta pendiente de cambio solo puede entrar a **Mi perfil** o cerrar sesión: cualquier
otra ruta la devuelve al perfil (`auth.exigir_cambio_contrasena`). La regla se revisa en
cada petición, así que la exigencia alcanza también a las sesiones ya abiertas. El perfil
rechaza repetir la contraseña actual.

Cada cambio de contraseña **cierra las sesiones abiertas** de esa cuenta: el identificador de
sesión de Flask-Login incluye `version_credencial`, que aumenta con cada cambio. Quien la
cambia desde su perfil conserva su sesión actual (y su llamada de voz en curso), y
cualquier copia de su cookie deja de servir.

**Por voz** (`admin/contrasenas` y `admin/usuarios`), sin decir nunca una contraseña: se
consulta el estado de cada cuenta y los intentos fallidos, se exige o retira el cambio
(una cuenta o todas las de gestores y conductores) y se restablece la contraseña. La de
una cuenta creada o restablecida por voz es **temporal**: Gaby lo dice en el resumen,
`credencial_usuario` lo indica (`debe_cambiar_contrasena: true`) y al entrar la persona
solo puede ir a cambiarla. *Asignar* una contraseña concreta queda solo en pantalla.

### Correo electrónico: solo caracteres permitidos

El correo del login, de las cuentas de acceso y de los clientes se valida con una **lista
blanca** (OWASP): en vez de buscar caracteres peligrosos, solo se acepta lo que un correo
necesita.

| Parte | Permitido |
|---|---|
| Antes de la `@` | Letras sin tilde, números y `.` `_` `-` `+` |
| `@` | Exactamente una |
| Dominio | Letras, números y `-`, separados por puntos, terminado en una extensión de letras |

Todo lo demás se rechaza: espacios, comillas, `< > ; = / \ ( ) { } | #`, tildes y la `ñ`.
Antes, el validador `Email` por sí solo aceptaba `'`, `=`, `/`, `|`, `{ }`, `#`, tildes y
`ñ`, porque el estándar RFC 5322 los permite.

- **La regla es una sola** (`campo_correo` en `app/controllers/seguridad.py`) y la usan los
  tres formularios. Si el alta aceptara un correo que el login rechaza, esa cuenta no podría
  entrar.
- **El servidor es la validación que cuenta.** En el navegador, el campo descarta al
  escribir o pegar cualquier carácter fuera de la lista, así que ni siquiera se pueden
  introducir. Eso es comodidad, porque se puede saltar.
- Los espacios de los extremos, que suelen llegar al copiar y pegar, se descartan antes de
  validar. El correo tiene un máximo de 120 caracteres, el de la columna.
- Un correo bien formado que no existe sigue recibiendo el mensaje genérico de credenciales
  inválidas, que no revela qué cuentas existen.

No es la única defensa contra inyección: las consultas pasan por el ORM con parámetros y
Jinja escapa lo que se muestra. Es defensa en profundidad.

### Bloqueo por intentos fallidos

Aplica a los cuatro roles, porque todos entran por el mismo login.

| Situación | Qué ve la persona |
|---|---|
| Fallos 1 a 4 | *Credenciales invalidas. Verifique sus datos. Intento fallido N de 5.* |
| Fallo 5 | *Acceso no valido: se superaron los 5 intentos permitidos. Espere 1 minuto para volver a intentar iniciar sesion.* |
| Cualquier intento durante ese minuto | El mismo mensaje, con los segundos que faltan. **Ni la contraseña correcta entra**, y ni siquiera se verifica |

- **El contador vuelve a cero** al entrar con la contraseña correcta, al cumplirse el
  minuto, o tras un día sin fallos. Los intentos durante el bloqueo no suman.
- **El contador es por correo escrito, no por cuenta.** También cuenta los correos que no
  existen, que reciben exactamente los mismos mensajes. Si solo se bloquearan las cuentas
  reales, el bloqueo revelaría qué correos están registrados. Por eso `intentos_acceso`
  no tiene clave ajena a `usuarios` y aparece suelta en el diagrama EER.
- **La contraseña actual que pide *Mi perfil* cuenta para el mismo bloqueo.** Si no, una
  sesión abierta permitiría adivinarla sin límite.
- El bloqueo afecta al inicio de sesión, no a las sesiones de pantalla ya abiertas. **Sí
  detiene una llamada de voz abierta**: mientras dure, sus funciones responden que la
  cuenta está bloqueada y el navegador cuelga. Por voz, los intentos solo se consultan;
  el bloqueo se levanta solo al minuto.
- En **Contraseñas**, la columna *Intentos fallidos* muestra los fallos acumulados de
  cada cuenta y, si está bloqueada, cuánto le falta.
- El límite y la espera se ajustan en `config.py` (`LOGIN_MAX_INTENTOS`,
  `LOGIN_BLOQUEO_SEGUNDOS`). La lógica está en `app/services/acceso.py`.
- *Cambiar correo* en *Mi perfil* también pide la contraseña actual y cuenta para el
  mismo bloqueo.

### Sesión única

Una cuenta tiene **una sola sesión abierta**. Si la misma persona inicia sesión en otro
navegador o dispositivo, la sesión anterior se cierra, y en su siguiente clic ese
navegador vuelve al login con el aviso *Su sesion se cerro porque la cuenta inicio sesion
en otro navegador o dispositivo.* Gana la sesión más reciente: así un navegador que se
cerró sin salir no deja la cuenta bloqueada.

Cada inicio de sesión genera un `token_sesion` nuevo, que va en el identificador de sesión
de Flask-Login junto a `version_credencial`. La cookie del otro navegador, y también su
cookie de *Mantener sesión iniciada*, conservan el token anterior y dejan de servir.
**Cerrar sesión borra el token**: ninguna copia de la cookie sigue abierta, ni en otra
pestaña ni en otro navegador. La lógica está en `app/services/sesiones.py`.

**Una llamada de voz vale lo mismo que la sesión que la abrió.** `sesiones_asistente`
guarda una huella (SHA-256) del identificador de esa sesión (`huella_sesion`, m013). Si la
cuenta entra en otro navegador, cierra sesión o le restablecen la contraseña, la llamada
deja de autorizar funciones, igual que la pestaña. Cambiar la propia contraseña en la
misma ventana no la corta: la llamada sigue a la sesión renovada.

### Cierre de sesión por inactividad

Una sesión sin peticiones durante el tiempo configurado se cierra. El administrador lo
ajusta en **Configuración > Sesión**, entre 1 y 480 minutos (30 por defecto), y el cambio
queda en la bitácora. Aplica desde la siguiente petición de cada sesión abierta.

- La última actividad se guarda en la base (`usuarios.sesion_actividad_en`), no en la
  cookie: *Mantener sesión iniciada* tampoco salta el límite al reabrir el navegador.
- Cumplido el tiempo, la pantalla pasa sola al login con el aviso *Su sesion se cerro tras
  N minutos sin actividad* (`static/js/sesion.js`). Antes de hacerlo consulta
  `/auth/sesion`, que no cuenta como actividad, porque otra pestaña pudo mantener viva la
  sesión. El cierre real lo hace el servidor (`auth.vigilar_sesion`), así que desactivar
  JavaScript no lo evita.
- **Hablar con Gaby es actividad.** Cada función de voz renueva la actividad de la sesión
  web, y durante la llamada el navegador consulta `/asistente/latido` cada 20 segundos
  (menos que el mínimo de 1 minuto). Así una conversación larga sin tocar la pantalla no
  cierra la sesión ni lleva la ventana al login, que cortaría la llamada. Si el latido
  responde que la llamada ya no está autorizada, el navegador cuelga y muestra el motivo.

### Cambiar el correo desde el perfil

*Mi perfil* permite cambiar la contraseña y el **correo de inicio de sesión**. El correo
pasa por la misma lista blanca del login, no puede ser el de otra cuenta y exige la
contraseña actual. La sesión sigue abierta, y el cambio queda en la bitácora como
*Cambiar correo propio*. Una cuenta con contraseña temporal primero debe definir la suya.

### Olvidé mi contraseña

El login ofrece **¿Olvidó su contraseña?** (`/auth/recuperar`). La persona escribe su
correo y recibe un enlace para definir una contraseña nueva, con la misma política de
siempre. La lógica está en `app/services/recuperacion.py`.

- **Pedir el enlace no cambia nada.** Solo quien abre el correo cambia la contraseña: nadie
  deja a otro sin acceso con solo escribir su correo.
- **No revela qué cuentas existen.** La pantalla responde lo mismo para un correo real, uno
  inexistente o una cuenta inactiva, y solo la cuenta activa recibe el enlace.
- **El enlace vence a los 30 minutos y es de un solo uso.** Es un token firmado con
  `SECRET_KEY` que lleva la `version_credencial` de la cuenta: al restablecer la contraseña
  la versión aumenta y el mismo enlace deja de servir. También caduca si la contraseña
  cambió por otra vía después de pedirlo. No se guarda en ninguna tabla.
- Se envía como máximo **un enlace por cuenta cada 5 minutos**.
- Al restablecerla se **cierran las sesiones abiertas** de la cuenta y se **levanta el
  bloqueo** por intentos fallidos: quien abrió el correo demostró ser el dueño.
- La solicitud y el cambio quedan en la bitácora (*Solicitar restablecimiento de
  contraseña*, *Restablecer contraseña por correo*), nunca con el enlace.

**Entrega del enlace.** Va al correo de la cuenta por su propio webhook de Make,
`MAKE_WEBHOOK_RESTABLECIMIENTO_URL` (ver el [escenario Rutalytics Privado](#escenario-rutalytics-privado-resumen-credenciales-y-restablecimiento)),
y se arma con `URL_PUBLICA`, **nunca con el `Host` de la petición**: ese lo escribe el
cliente, y un atacante podría hacer llegar a la víctima un enlace válido hacia su propio
dominio. Sin ese webhook y `URL_PUBLICA`:

| Entorno | Comportamiento |
|---|---|
| Desarrollo | El enlace se escribe en la **consola del servidor** (`Desarrollo, sin MAKE_WEBHOOK_RESTABLECIMIENTO_URL: enlace para restablecer...`) |
| Producción | El login no ofrece la opción y `/auth/recuperar` pide acudir al administrador |

### Datos personales cifrados

En `usuarios`, la **contraseña** se guarda como hash PBKDF2-SHA256 (no se puede revertir), y
el **documento** y el **teléfono** se guardan **cifrados** con Fernet (AES-128 con
HMAC-SHA256, de la librería `cryptography`). El ORM cifra al guardar y descifra al leer
(`TextoCifrado` en `app/cifrado.py`), así que la aplicación los muestra en claro y la base
no. La guía `docs/seguridad/Seguridad_Contrasenas_SGDS.md` trae las consultas SQL para
comprobarlo.

- Cada valor lleva un IV aleatorio: el mismo dato cifrado dos veces da textos distintos, y
  la base no revela ni siquiera que dos cuentas comparten un dato.
- **Índice ciego del documento.** El texto cifrado no se puede comparar en SQL, así que
  `documento_indice` guarda un HMAC-SHA256 del documento con otra clave. Con él se exige
  que no se repita (también con un índice único en la base) y se busca por igualdad: en
  *Usuarios* el documento se encuentra **completo**, no por un fragmento.
- Una columna cifrada **rechaza toda comparación en SQL** salvo `IS NULL`: una consulta que
  la compare por error falla con un mensaje claro, en vez de no encontrar nada en silencio.
- **La bitácora no guarda estos datos completos**: en las acciones sobre cuentas, en
  pantalla y por voz, quedan enmascarados (`******4567`), y el resumen que lee la voz dice
  *"uno terminado en 4567"*. Los datos de los clientes son de empresas (NIT, teléfono de
  contacto) y siguen visibles en su auditoría.
- **Clave.** Las claves de cifrado y del índice se derivan con HKDF de `CLAVE_CIFRADO`.
  **Es obligatoria**: en producción la aplicación no arranca sin ella, y en desarrollo, si
  falta, se deriva de `SECRET_KEY` (cambiar esta tendría el mismo efecto). **No la cambie
  ni la pierda una vez cifrados los datos**: con otra clave los documentos y teléfonos ya
  guardados no se pueden leer, y la aplicación lo dice en el error. Guarde una copia fuera
  del repositorio. En Render, `render.yaml` la genera una sola vez.
- **Por voz, con los valores descifrados.** Gaby encuentra una cuenta por su documento
  (por el índice ciego, como la pantalla) o su teléfono (comparando el valor ya descifrado),
  y lee solo sus últimos cuatro dígitos. Ningún texto cifrado llega a la auditoría: si uno
  apareciera en un detalle o un resumen, se reemplaza por *"(no se registra)"*.
- El correo **no se cifra**: es el usuario con el que se inicia sesión, y el login, la
  unicidad y la búsqueda dependen de él.

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
| Un administrador intenta restablecerse o exigirse el cambio de su propia contraseña | Bloqueado: la cambia con la actual, en *Contraseñas* o en *Mi perfil* |
| Un administrador intenta asignar, restablecer o exigir el cambio de la contraseña de un cliente | Bloqueado: solo el cliente cambia la suya |
| Un administrador intenta lo mismo sobre otro administrador | Bloqueado: solo cada administrador cambia la suya |
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

### Archivos que no son CSV

**Manda el contenido, no la extensión.** El nombre y el tipo MIME los escribe quien sube
el archivo: renombrar un ejecutable a `.pdf` o a `.csv` no lo convierte en eso. Antes de
guardar o procesar nada, `app/services/archivos.py` revisa la **firma** del archivo (los
primeros bytes de cada formato) y rechaza todo lo que no sea texto:

| Contenido detectado | Mensaje |
|---|---|
| Programa de Windows (`MZ`), Linux (ELF), macOS (Mach-O) o un script (`#!/`) | *Archivo rechazado por seguridad: «factura.pdf» no es un CSV, es un programa ejecutable de Windows (.exe o .dll). No se guardó ni se procesó.* |
| PDF, ZIP o Excel/Word (`.xlsx`, `.docx`, `.xls`, `.doc`), RAR, 7z, gzip, PNG, JPEG, GIF | *Archivo rechazado: … es un documento PDF …* |
| Texto en UTF-16 | Pide guardarlo como CSV UTF-8 |
| Cualquier otro archivo con bytes nulos | *… es un archivo binario, no de texto …* |

- El archivo rechazado **no se guarda** en el servidor ni llega al lector de CSV. Antes, un
  binario se decodificaba como Latin-1 (que acepta cualquier byte) y fallaba después,
  por las columnas.
- Cada rechazo queda en la **Bitácora** como *Rechazar archivo*, con resultado *Error*,
  el nombre del archivo, su tamaño, lo que se detectó y quién lo subió.
- Sin falsos positivos con un CSV real: UTF-8 (con o sin BOM de Excel), Latin-1, o un
  texto que empiece por `MZ` siguen entrando.
- La revisión va **antes** de la extensión: un `.exe` renombrado a `.pdf` se rechaza por ser
  un ejecutable, no solo por no terminar en `.csv`.
- **Tamaño:** el CSV admite 2 MB, y toda petición está limitada a 5 MB
  (`MAX_CONTENT_LENGTH`). Un envío mayor se corta antes de leerlo, con una página 413.

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
confirmación y quedan en la auditoría). En total son **42 funciones con 94 operaciones**
(antes de integrar la seguridad, 37 con 74): conductor 6 funciones (8 operaciones), gestor
20 (45), administrador 28 (78; antes 23 con 58) y cliente 8 (8).

| Rol | Funciones | Total |
|---|---|---|
| **Conductor** | Consultas: `mi-ruta`, `siguiente-parada`, `detalle-parada`, `mi-historial` (últimas 5 rutas). `iniciar-ruta` (M). `parada`: `en-camino` (M, también reintenta una fallida), `fallo` (M, con el catálogo o "Otro"), `entregar` (M, con nombre y documento de quien recibe); `entregar` y `fallo` aceptan una observación dictada | 6 |
| **Gestor logístico** | Consultas: `resumen-dia`, `pendientes-sin-ruta`, `avance-rutas`, `buscar-pedido`, `stock-producto`, `productos-bajo-minimo`, `fallidos-hoy`, `solicitudes-contacto-pendientes`. Analítica, con los periodos del panel: `tiempo-promedio-entrega`, `cumplimiento-ventana`, `productividad-conductores`, `causas-fallo`. `atender-solicitud` (M). `resumen-diario`: enviar (M, el resumen del día al correo de operaciones). `pedidos`: crear, editar, anular, reintentar (M). `productos`: crear, editar, activar, desactivar, entrada, ajuste (M). `rutas`: consultar, crear, agregar-parada, quitar-parada, recalcular, eliminar (M salvo consultar). `clientes` y `sedes`: consultar, crear, editar, activar, desactivar (M salvo consultar). `vehiculos`: consultar, crear, editar, activar, desactivar (M salvo consultar) | 20 |
| **Administrador** | Todas las del gestor; `resumen-rendimiento` (el panel de rendimiento, solo suyo como en pantalla); `usuarios`: consultar, crear, editar, activar, desactivar, restablecer-contrasena (M salvo consultar); `configuracion`: consultar, editar-centro, editar-inactividad, agregar-motivo, renombrar-motivo, activar-motivo, desactivar-motivo (M salvo consultar); `bitacora` (últimos registros, con filtros por fecha, usuario y entidad); `roles`: consultar, crear, editar, agregar-permiso, quitar-permiso, eliminar (M salvo consultar); `permisos` de una cuenta: consultar, asignar-rol, quitar-rol, conceder, denegar, quitar-ajuste (M salvo consultar); `contrasenas`: consultar, intentos, exigir-cambio, quitar-exigencia, exigir-cambio-masivo (M salvo consultar e intentos); `errores` (los recientes o uno por su número) | 28 |
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
  genera una **temporal** y la envía solo en `credencial_usuario`, por su propio webhook,
  al correo del usuario; el resumen y la respuesta de Gaby avisan que deberá cambiarla al
  iniciar sesión. Sin `MAKE_WEBHOOK_CREDENCIALES_URL`, esas dos operaciones se rechazan ya
  en el resumen. El resumen de editar lee las consecuencias (un vehículo que queda sin
  conductor, un cliente que pierde el portal); ver [Salvaguardas](#salvaguardas).
- **Roles, permisos y contraseñas.** Las reglas de [Roles y permisos](#roles-y-permisos) y
  de [Contraseñas](#contraseñas), con los servicios de la pantalla (`permisos`,
  `usuarios.cambiar_exigencia`): gestionar usuarios, contraseñas y roles no se reparte;
  *Administrador* y *Cliente* no se editan; un administrador o un cliente no llevan
  ajustes, y su contraseña no la exige otro. Los permisos se nombran como en pantalla
  (*"ver pedidos"*, *"gestionar inventario"*).
- **Entregas.** El conductor confirma una entrega por voz solo con el nombre y el
  documento de quien recibe; descuenta el inventario igual que la pantalla. La llamada
  no envía la ubicación del teléfono, así que la prueba de la entrega o del fallo queda
  sin coordenadas y con la observación *"Registrada por el asistente de voz, sin
  ubicación"*; si el conductor dicta una observación, se agrega después (*"... sin
  ubicación. Observación: dejé el pedido en portería"*). Iniciar la ruta, en camino, entregar y fallo quedan en la auditoría sin
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
| Todos | Iniciar y cerrar sesión, el botón que abre la llamada | La llamada parte de una sesión iniciada |
| Todos | Cambiar la propia contraseña (*Mi perfil*) | Las contraseñas nunca se dicen por voz |
| Todos | Cambiar el propio correo de inicio de sesión (*Mi perfil*) | Es la identidad de la cuenta, como la contraseña, y pide la contraseña actual |
| Todos | *Olvidé mi contraseña* y el enlace para restablecerla | Son para quien no puede iniciar sesión, y la llamada parte de una sesión iniciada; las contraseñas nunca se dicen por voz |
| Gestor y admin | Subir o revisar archivos: importar pedidos desde CSV | Exige elegir un archivo y revisar su contenido en pantalla; no tiene equivalente hablado |
| Admin | Escribir una contraseña para otra cuenta (*Asignar* en *Contraseñas*) | Las contraseñas nunca se dicen por voz; por voz, *restablecer-contrasena* genera una temporal y la envía al correo |
| Todos | Cambiar el propio correo de avisos (*Mi perfil*) | Es un dato de la propia cuenta que se cambia en Perfil, como el propio correo; el administrador lo cambia por voz con `usuarios:editar` |
| Admin | Cambiar el correo de inicio de sesión de una cuenta | Es la identidad de la cuenta: solo en pantalla |
| Admin | Provocar un error en la pantalla de errores | Es una herramienta de diagnóstico de pantalla; por voz solo ensuciaría el registro |
| Admin | Desbloquear una cuenta bloqueada por intentos | Tampoco existe en pantalla: el bloqueo se levanta solo al minuto. Por voz se consultan los intentos |
| Conductor | La ubicación del teléfono al entregar o registrar un fallo | La llamada no envía la ubicación; la prueba de entrega por voz lo dice en su observación |
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
     fue desactivada o cambió de rol, su sesión web ya no es la que abrió la llamada
     (otro navegador, cierre de sesión, contraseña restablecida), su inicio de sesión
     está bloqueado por intentos o debe cambiar su contraseña. El 403 trae en `mensaje`
     el motivo, para que Gaby lo diga y termine la llamada;
   - el rol de la sesión debe estar entre los que la función declara → **403** si no:
     una sesión de un rol no puede llamar las funciones de otro. El administrador usa
     las del gestor por su misma URL;
   - la cuenta debe tener el permiso de la pantalla equivalente (`PERMISOS_VOZ`) → si no,
     200 con *"No tienes el permiso «...»"*.
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
por SKU o por nombre aproximado (*"arros"*, *"detergente dos kilos"*); un cliente por
nombre, NIT (con o sin dígito de verificación) o teléfono; una cuenta por nombre, correo,
documento o teléfono (descifrados, ver [Datos personales cifrados](#datos-personales-cifrados));
una sede o un conductor por nombre. Si hay varias coincidencias, la respuesta lista hasta
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

La llamada vive en la página donde se inició, así que el navegador la cortaría al cambiar
de página. Para seguir hablando mientras se navega, con una llamada en curso los enlaces
y formularios de la aplicación se abren en un marco que cubre la ventana: la página de la
llamada queda debajo con solo el botón, y la barra de direcciones y el título siguen al
marco. Recargar o cerrar la pestaña corta la llamada, y si el marco llega al login (*Salir*
o inactividad), la ventana entera va al login y la llamada termina.

Para ver lo que el asistente acaba de cambiar sin salir del apartado, **Pedidos, Rutas,
Inventario, Clientes y Analítica** llevan un botón **Actualizar** (el icono de la flecha
circular) junto al título; en el **Tablero** va junto a *Operación de hoy*, que es lo que
cambia por voz. El botón vuelve a pedir la página y reemplaza solo el contenido, sin
recargarla: la llamada sigue, se conservan los filtros y el desplazamiento, y no se suman
entradas al historial. Si la sesión venció lleva al login; si falla la conexión, deja lo
que se ve y lo avisa. Ver `app/static/js/actualizar.js`.

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
(`/admin/bitacora/`), con filtros por fecha, usuario, origen y acción. El endpoint
`GET /api/automatizacion/auditoria` (con `AUTOMATIZACION_TOKEN`) la entrega por lotes, sin
huecos ni repetidos, para quien la quiera llevar a otra herramienta; ningún escenario de
Make lo usa hoy.

### Registro de errores

Cada excepción no controlada de una petición queda en la tabla `errores_sistema` con
fecha, usuario, IP, método y ruta, clase, mensaje y traza. También queda en
`instance/logs/errores.log`, que rota a 1 MB y es el respaldo si la falla es justamente de la
base. El administrador lo consulta en **Errores** (`/admin/errores/`, en el menú
*Administración*), del más reciente al más antiguo.

- La página 500 muestra el número del registro (*referencia #N*) para encontrarlo en la
  pantalla.
- **Provocar un error**, en la misma pantalla, lanza un error a propósito para comprobar
  que el registro lo captura.
- Se registra también en modo desarrollo, donde el depurador muestra la traza en lugar de
  la página 500 (la señal `got_request_exception` de Flask se emite en ambos casos).
- No guarda los datos del formulario ni la consulta de la URL, que pueden traer
  contraseñas o tokens. La lógica está en `app/services/errores.py`.

## Automatizaciones con Make

La aplicación **envía** avisos a [Make](https://www.make.com); Make nunca la llama. Todo es
opcional: sin las variables correspondientes la aplicación funciona igual y no sale nada.
Hay dos escenarios, cada uno con su webhook, y funcionan igual en el PC y en el servidor:
solo cambian las variables de entorno.

| Escenario | Variables (su webhook) | Recibe | Hoja |
|---|---|---|---|
| **SGDS** | `MAKE_WEBHOOK_URL` | Los avisos de la operación: `pedido_estado`, `stock_bajo`, `solicitud_contacto`, `accion_asistente` | Pestaña *Notificaciones* |
| **Rutalytics Privado**, con **"Data is confidential"** | `MAKE_WEBHOOK_RESUMEN_URL`, `MAKE_WEBHOOK_CREDENCIALES_URL` y `MAKE_WEBHOOK_RESTABLECIMIENTO_URL`, **las tres con la misma URL** | `resumen_diario`, `credencial_usuario` y `restablecimiento_contrasena` | Pestañas *Resumen diario* y *Notificaciones*, sin contraseñas ni enlaces |

Las dos escriben en la hoja de Google Sheets **"SGDS - Bitacora"**. Los dos webhooks usan la
misma API key, `MAKE_WEBHOOK_KEY`.

### Aviso → destinatario

Cada aviso llega al correo de cada perfil según su rol. Sale **un evento por
destinatario**, cada uno con su `correo_destino`, así que los escenarios de Make no
cambian: cada ejecución manda un correo.

| Aviso | Destinatario |
|---|---|
| `pedido_estado` | El correo del cliente (pantalla y voz), como siempre |
| `stock_bajo`, `solicitud_contacto` | Todos los **gestores logísticos** activos |
| `resumen_diario` | Los **gestores** y los **administradores** activos |
| `accion_asistente` | Los **administradores** activos |
| `ruta_asignada` | El **conductor** de la ruta |
| `credencial_usuario`, `restablecimiento_contrasena` | El propio usuario, en su correo de **inicio de sesión** |

- **Correo de avisos.** Cada cuenta puede tener uno (`usuarios.correo_avisos`): lo fija el
  administrador al crearla o editarla en *Usuarios* (o por voz, con `usuarios:crear` y
  `usuarios:editar`) y la propia persona en *Mi perfil*. Si está vacío, los avisos van a
  su correo de inicio de sesión. Las contraseñas y el enlace de restablecimiento, en
  cambio, siempre van al de inicio de sesión: es el que prueba que la cuenta es suya.
- **`CORREO_OPERACIONES` es solo el respaldo**: recibe un aviso interno cuando no hay
  ninguna cuenta activa de ese rol con correo. Sin él, en ese caso el aviso no sale.
- Las cuentas inactivas no reciben avisos, y se respetan las reglas de `avisar=False`
  (el flujo del conductor y las entradas de mercancía no envían `accion_asistente`).

### Variables

| Variable | Para qué | Si está vacía |
|---|---|---|
| `MAKE_WEBHOOK_URL` | Webhook del escenario **SGDS**: los avisos de la operación | No se envía ningún aviso de la operación |
| `MAKE_WEBHOOK_KEY` | API key de los dos webhooks; viaja en el encabezado `x-make-apikey` | Los avisos salen sin el encabezado |
| `MAKE_WEBHOOK_RESUMEN_URL` | Webhook de **Rutalytics Privado**: `resumen_diario` | El resumen no se envía, y el botón, Gaby y el comando dicen por qué |
| `MAKE_WEBHOOK_CREDENCIALES_URL` | Webhook de **Rutalytics Privado**: `credencial_usuario` | No se envían contraseñas, y crear o restablecer cuentas **por voz** se rechaza en el resumen (en pantalla funciona igual) |
| `MAKE_WEBHOOK_RESTABLECIMIENTO_URL` | Webhook de **Rutalytics Privado**: `restablecimiento_contrasena` (requiere `URL_PUBLICA`) | En producción no se ofrece *Olvidé mi contraseña*; en desarrollo el enlace sale en la consola del servidor |
| `CORREO_OPERACIONES` | **Respaldo** de los avisos internos (`stock_bajo`, `solicitud_contacto`, `accion_asistente`, `resumen_diario`) cuando no hay ninguna cuenta activa del rol con correo | En ese caso el aviso no sale |
| `URL_PUBLICA` | Dirección de acceso que lleva `credencial_usuario` (`url_acceso`) y con la que se arma el enlace de restablecimiento | `url_acceso` va vacía y el enlace no se envía |
| `SEMILLA_CORREO_ADMIN`, `SEMILLA_CORREO_GESTOR`, `SEMILLA_CORREO_CONDUCTOR` | Solo la demo: `seed.py` los pone como correo de avisos de `admin@sgds.com`, `despachador@sgds.com` y `conductor1@sgds.com` | Esas cuentas reciben los avisos en su correo de inicio de sesión |
| `AUTOMATIZACION_TOKEN` | Token de los endpoints de respaldo `GET /api/automatizacion/resumen-diario` y `/auditoria` (ningún escenario los usa) | Ambos endpoints responden **404** |

Genere el token con `python -c "import secrets; print(secrets.token_urlsafe(32))"`.

### Eventos salientes

Todo aviso es un `POST` JSON con un campo **`tipo`**: los de la operación a
`MAKE_WEBHOOK_URL`, y `resumen_diario`, `credencial_usuario` y `restablecimiento_contrasena`
cada uno a su variable (el webhook de Rutalytics Privado). Todos usan el mismo mecanismo:

- Solo sale **después del commit**: un cambio revertido no genera correo.
- Va en segundo plano, con un timeout de 3 s de conexión y 5 s de respuesta. **Si Make
  falla o tarda, la operación no se bloquea ni se revierte**; el fallo queda solo en el log
  y el aviso no se reintenta. La bitácora de la aplicación sigue teniendo cada acción.
- Todo evento lleva **`fecha_hora`** en ISO 8601 con segundos y el desplazamiento de Bogotá
  (`2026-10-06T11:20:37-05:00`), y todos salvo `resumen_diario` también `hora`
  (`2026-10-06 11:20`); el resumen trae en cambio `fecha`.

| `tipo` | Cuándo | Destinatario | Campos |
|---|---|---|---|
| `pedido_estado` | Un pedido pasa a **EN_RUTA, ENTREGADO o FALLIDO** desde cualquier origen (vista móvil, "Iniciar ruta" o asistente de voz), o se **anula** (CANCELADO) | El cliente, en `correo`. Si no tiene correo, no se envía | `codigo`, `estado`, `cliente`, `correo`, `direccion`, `ventana`, `hora`, `fecha_hora`; en FALLIDO (motivo de la prueba de entrega) y en CANCELADO (motivo de la anulación), además `motivo` |
| `stock_bajo` | Una entrega o un movimiento manual **cruza** un umbral del stock de un producto activo (ver abajo) | Cada gestor logístico activo, en `correo_destino` | `sku`, `producto`, `stock_actual`, `stock_minimo`, `negativo`, `pedido` (código del pedido entregado, o `null` si fue manual), `correo_destino`, `hora`, `fecha_hora` |
| `solicitud_contacto` | Un cliente pide por el asistente de voz que el gestor lo contacte (una pendiente a la vez por cliente) | Cada gestor logístico activo, en `correo_destino` | `cliente`, `correo`, `telefono`, `motivo`, `hora`, `fecha_hora`, `correo_destino` |
| `accion_asistente` | Se agrega a la **auditoría** un registro con correo: una acción confirmada por voz (con su resultado), salvo el flujo del conductor y las entradas de mercancía, o una acción de *Usuarios* o *Configuración* (ver [Auditoría](#auditoría-y-bitácora)) | Cada administrador activo, en `correo_destino` | `id_auditoria`, `usuario`, `rol`, `accion`, `entidad`, `resumen`, `detalle` (texto plano, una línea por dato), `resultado` (`exito` o `error`), `hora`, `fecha_hora`, `correo_destino`. **Nunca lleva contraseñas ni tokens** |
| `ruta_asignada` | Se **crea** una ruta, se le **agrega o quita** una parada, se **recalcula** o se **elimina**, en pantalla o por voz, si su fecha es hoy o futura. Un evento por operación | El conductor de la ruta, en `correo_destino` (su correo de avisos) | `conductor` (nombre), `correo_destino`, `ruta` (código), `fecha` (`DD/MM/AAAA`), `paradas` (las que tiene la ruta después del cambio; 0 si se eliminó), `accion` (`creada`, `actualizada` o `eliminada`), `detalle_ruta` (una frase: *"Se agregó la parada de Tienda La Esquina"*), `hora`, `fecha_hora` |
| `credencial_usuario` | Se **crea o restablece por voz** una cuenta: la contraseña se genera en el servidor | El propio usuario, en `correo_destino` (su correo de inicio de sesión) | `nombre`, `correo`, `contrasena`, `debe_cambiar_contrasena` (siempre `true`: es temporal), `indicaciones` (el texto para el correo), `url_acceso` (`URL_PUBLICA`), `creado_por` (nombre y rol), `accion` (`creación` o `restablecimiento`), `hora`, `fecha_hora`, `correo_destino`. Es el **único** lugar por el que viaja la contraseña: no se guarda en la auditoría ni sale en otro evento, y va **solo** a `MAKE_WEBHOOK_CREDENCIALES_URL`. En pantalla la contraseña se sigue mostrando una vez, como antes, y no se envía |
| `resumen_diario` | Alguien lo pide con **Enviar resumen del día** en el tablero o por voz, o lo dispara el comando programado `enviar-resumen` (ver [Resumen diario](#resumen-diario-lo-envía-la-app)) | Cada gestor y administrador activo, en `correo_destino` | Exactamente los de `GET /api/automatizacion/resumen-diario`, en la raíz: `fecha`, `fecha_hora`, `correo_destino`, `kpis` (anidado), `productos_bajo_minimo`, `fallidos_para_reprogramar`, `resumen_html`. Va **solo** a `MAKE_WEBHOOK_RESUMEN_URL` |
| `restablecimiento_contrasena` | Alguien pide *Olvidé mi contraseña* para una cuenta activa (como máximo uno cada 5 minutos) | La propia persona, en `correo_destino` (su correo de inicio de sesión) | `nombre`, `correo`, `enlace` (armado con `URL_PUBLICA`, de un solo uso), `vence_en_minutos` (30), `hora`, `fecha_hora`, `correo_destino`. **Nunca lleva la contraseña**, ni la actual ni una nueva: solo el enlace para definirla. Va **solo** a `MAKE_WEBHOOK_RESTABLECIMIENTO_URL` y no se guarda en la auditoría |

**Todo evento lleva además quién originó la acción:**

| Campo | Contenido |
|---|---|
| `realizado_por` | Nombre completo de quien hizo la acción, o `Sistema SGDS (automático)` |
| `rol_realizado_por` | `Conductor`, `Gestor logístico`, `Administrador`, `Cliente` o `Sistema` |
| `origen` | `pantalla`, `voz` o `automático` |
| `realizado_por_publico` | Solo la etiqueta del rol. Es el que deben usar los correos a clientes (`pedido_estado`): no exponen nombres del personal |

En `stock_bajo` es quien hizo el movimiento que cruzó el umbral (en una entrega, el
conductor que la confirmó). En `resumen_diario` (y en cada fila de reprogramación) es
quien lo pidió, desde la pantalla o por voz, o `Sistema SGDS (automático)`, con origen
`automático`, si lo envió el comando programado; el GET de respaldo siempre sale como el
sistema.

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
  "debe_cambiar_contrasena": true,
  "indicaciones": "Esta contraseña es temporal: al iniciar sesión el sistema le pedirá cambiarla por una propia antes de continuar.",
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

```json
{
  "tipo": "ruta_asignada",
  "conductor": "Andres Molina",
  "correo_destino": "andres@ejemplo.com",
  "ruta": "RUT-20261103-01",
  "fecha": "03/11/2026",
  "paradas": 3,
  "accion": "actualizada",
  "detalle_ruta": "Se agregó la parada de Tienda La Esquina",
  "hora": "2026-11-03 09:12",
  "fecha_hora": "2026-11-03T09:12:40-05:00",
  "realizado_por": "Carlos Rueda",
  "rol_realizado_por": "Gestor logístico",
  "origen": "voz",
  "realizado_por_publico": "Gestor logístico"
}
```

```json
{
  "tipo": "restablecimiento_contrasena",
  "nombre": "Andres Molina",
  "correo": "conductor1@sgds.com",
  "enlace": "https://sgds.ejemplo.com/auth/restablecer/eyJ1IjozLCJ2IjoxfQ.ZyX9Qw.k3J...",
  "vence_en_minutos": 30,
  "hora": "2026-11-02 08:15",
  "fecha_hora": "2026-11-02T08:15:42-05:00",
  "correo_destino": "conductor1@sgds.com",
  "realizado_por": "Andres Molina",
  "rol_realizado_por": "Conductor",
  "origen": "pantalla",
  "realizado_por_publico": "Conductor"
}
```

### Escenario "SGDS": avisos de la operación

1. Cree un escenario con el disparador **Webhooks → Custom webhook** y copie su URL en
   `MAKE_WEBHOOK_URL`. Asígnele una *API key* y cópiela en `MAKE_WEBHOOK_KEY`: Make
   rechazará los avisos que no la traigan.
2. Pulse *Redetermine data structure* y envíe el aviso de estructura (todos los campos en
   uno, ver [Ejemplos](#ejemplos-para-make-sin-la-app)) para que Make aprenda los campos.
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
     `{{resultado}}`.
   - `tipo` = `ruta_asignada` → correo para `{{correo_destino}}` (el conductor) con
     `{{ruta}}`, `{{fecha}}`, `{{paradas}}`, `{{accion}}` y `{{detalle_ruta}}`.
   - En los correos a clientes use `{{realizado_por_publico}}`, nunca `{{realizado_por}}`.
4. Al final de cada ruta, **Google Sheets → Add a Row** en la pestaña *Notificaciones* de
   la hoja "SGDS - Bitacora": `fecha_hora`, `tipo`, el destinatario (`correo` o
   `correo_destino`), un asunto corto, `realizado_por` y `origen`.

Este escenario **no** tiene rutas para `resumen_diario`, `credencial_usuario` ni
`restablecimiento_contrasena`: esos eventos nunca llegan a `MAKE_WEBHOOK_URL`. Si su
escenario ya tenía alguna, elimínela.

### Escenario "Rutalytics Privado": resumen, credenciales y restablecimiento

Recibe lo que no debe quedar en un historial: la contraseña temporal de una cuenta
creada por voz, el enlace que permite definir una contraseña y el resumen del día.

1. Cree un escenario **separado** con **Webhooks → Custom webhook**, con la misma API key
   (`MAKE_WEBHOOK_KEY`). Copie su URL en **las tres** variables:
   `MAKE_WEBHOOK_RESUMEN_URL`, `MAKE_WEBHOOK_CREDENCIALES_URL` y
   `MAKE_WEBHOOK_RESTABLECIMIENTO_URL`. Defina también `URL_PUBLICA`.
2. En *Scenario settings* active **"Data is confidential"**: Make deja de guardar el
   contenido de cada ejecución, así que ni la contraseña ni el enlace quedan en el
   historial. Compártalo solo con quien administre las cuentas.
3. *Redetermine data structure* con el aviso `estructura-privado` del
   [script de ejemplos](#ejemplos-para-make-sin-la-app): trae todos los campos de los tres
   tipos, con `kpis` anidado y un elemento en cada lista.
4. Un **Router** con tres rutas, cada una con un filtro sobre `tipo`:
   - `tipo` = `resumen_diario` → correo para `{{correo_destino}}` con el contenido en
     **HTML** y el cuerpo `{{resumen_html}}`. Luego **Google Sheets → Add a Row** en la
     pestaña *Resumen diario*: `fecha`, `kpis.total`, `kpis.entregados`, `kpis.fallidos`,
     `kpis.cancelados`, `kpis.pendientes`, `kpis.tasa_exito`, `kpis.cumplimiento_ventana`,
     cuántos productos bajo el mínimo y cuántos fallidos para reprogramar (`length()` de
     cada lista), `realizado_por` y `origen`. Opcional: un **Iterator** sobre
     `fallidos_para_reprogramar` y un correo por pedido a `{{correo}}`, para que el cliente
     coordine otra visita.
   - `tipo` = `credencial_usuario` → correo para `{{correo_destino}}` (el propio usuario)
     con `{{url_acceso}}`, `{{correo}}`, `{{contrasena}}` e `{{indicaciones}}`, que le dice
     que es temporal y que la cambiará al entrar.
   - `tipo` = `restablecimiento_contrasena` → correo para `{{correo_destino}}` con
     `{{nombre}}`, el enlace `{{enlace}}` y su vigencia, `{{vence_en_minutos}}` minutos, y un
     texto como *"Si no lo pidió, ignore este correo: su contraseña no cambia"*.
5. En las rutas de credenciales y restablecimiento, después del correo, **Add a Row** en
   la pestaña *Notificaciones*: `fecha_hora`, `tipo`, `correo_destino`, `accion` (o
   "restablecimiento"), `realizado_por` y `origen`. **Nunca** mapee `contrasena` ni
   `enlace` a la hoja ni a otro módulo que no sea el correo.

Sin `MAKE_WEBHOOK_CREDENCIALES_URL`, Gaby no ofrece crear ni restablecer cuentas por voz:
responde, ya en el resumen, que se hace en pantalla.

### Resumen diario: lo envía la app

El resumen del día ya no lo consulta Make en un horario: **la app lo envía**, como evento
`resumen_diario`, al escenario Rutalytics Privado. Lo arma `app/services/resumen_diario.py`,
la misma función que responde `GET /api/automatizacion/resumen-diario`, que queda como
respaldo sin cambios (con `AUTOMATIZACION_TOKEN`). Tres formas de dispararlo, todas con
el mismo servicio y todas en la bitácora (*Enviar resumen del día*):

| Desde | Quién | Origen del evento |
|---|---|---|
| Botón **Enviar resumen del día** del tablero | Quien tenga el permiso *Enviar el resumen del día* (por defecto administrador y gestor; se reparte en *Roles*) | Quien lo pidió, `pantalla` |
| Gaby: *"envía el resumen del día"* (`gestor/resumen-diario`, operación `enviar`) | Gestor y administrador, con el mismo permiso. Confirma antes: *"Voy a enviar el resumen de hoy a operaciones@... ¿confirmas?"* | Quien lo pidió, `voz` |
| `flask --app run enviar-resumen`, programado | El Programador de tareas de Windows o el cron del servidor | `Sistema SGDS (automático)`, `automático` |

- `kpis` sale de las mismas funciones que el tablero y la analítica, así que el correo y
  la pantalla siempre muestran las mismas cifras. `cumplimiento_ventana` es `null` si hoy
  no hubo entregas con ventana horaria.
- `fallidos_para_reprogramar` lista los pedidos FALLIDOS de hoy cuyo cliente tiene correo.
- `resumen_html` es el correo ya armado en español, con estilos en línea y los datos de
  clientes escapados.
- Sin `MAKE_WEBHOOK_RESUMEN_URL` o sin `CORREO_OPERACIONES` no sale nada: el botón lo dice
  en pantalla, Gaby lo dice sin pedir confirmación y el comando termina con código 1.

```json
{
  "tipo": "resumen_diario",
  "fecha": "2026-11-02",
  "fecha_hora": "2026-11-02T18:00:04-05:00",
  "correo_destino": "operaciones@ejemplo.com",
  "kpis": {
    "total": 6, "entregados": 2, "fallidos": 1, "cancelados": 0, "pendientes": 3,
    "tasa_exito": 66.7, "cumplimiento_ventana": 100.0
  },
  "productos_bajo_minimo": [
    {"sku": "SKU-1004", "producto": "Aceite vegetal 1 L", "stock_actual": 30,
     "stock_minimo": 45, "negativo": false}
  ],
  "fallidos_para_reprogramar": [
    {"codigo": "PED-20261102-005", "cliente": "Supermercado El Portal",
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

**Programarlo en Windows (Programador de tareas)**, a las 18:00 todos los días, desde
`C:\Proyecto-Logistica`. El comando lee el `.env` de la carpeta del proyecto, así que la
tarea debe arrancar en ella:

```bat
schtasks /Create /TN "Rutalytics - Resumen diario" /SC DAILY /ST 18:00 /F /TR "cmd /c cd /d C:\Proyecto-Logistica && C:\Proyecto-Logistica\.venv\Scripts\flask.exe --app run enviar-resumen >> C:\Proyecto-Logistica\instance\logs\resumen.log 2>&1"
```

- `instance\logs` la crea la aplicación al arrancar. Si nunca la ha corrido en ese equipo,
  créela antes (`mkdir instance\logs`): `cmd` abre el archivo del `>>` antes de que corra
  el comando.
- Probarla sin esperar: `schtasks /Run /TN "Rutalytics - Resumen diario"`, y revise
  `instance\logs\resumen.log` (y que llegue el correo).
- Ver su estado y el último resultado: `schtasks /Query /TN "Rutalytics - Resumen diario" /V /FO LIST`.
  *Último resultado* 0 es éxito; 1, que faltaba una variable (el motivo queda en el log).
- Quitarla: `schtasks /Delete /TN "Rutalytics - Resumen diario" /F`.
- La tarea corre solo con la sesión de Windows iniciada. Para que corra aunque no lo esté,
  ábrala en el Programador de tareas y marque *Ejecutar tanto si el usuario inició sesión
  como si no*. La base (el contenedor de MySQL) debe estar arriba a esa hora.

En el servidor se programa con el cron del proveedor: ver [Despliegue](#despliegue).

### Ejemplos para Make sin la app

`ejemplos/make/enviar_ejemplos_make.ps1` envía avisos de ejemplo con los mismos campos que
arma la app, para que Make aprenda la estructura y para probar los escenarios. Lee las URLs
del `.env` (aunque la línea esté comentada) y manda todos los correos a
`CORREO_OPERACIONES`:

```bat
powershell -ExecutionPolicy Bypass -File .\ejemplos\make\enviar_ejemplos_make.ps1 -Modo estructura
```

| Modo | Escenario | Qué envía |
|---|---|---|
| `estructura` | SGDS | Un aviso de tipo `estructura` con todos los campos de la operación |
| `avisos` | SGDS | `pedido_estado`, `stock_bajo`, `solicitud_contacto` y `accion_asistente` |
| `ruta` | SGDS | Un `ruta_asignada` de ejemplo (los campos de `estructura` también los traen) |
| `estructura-privado` | Rutalytics Privado | Un aviso de tipo `estructura` con todos los campos de `resumen_diario`, `credencial_usuario` y `restablecimiento_contrasena` (con `kpis` anidado y un elemento en cada lista), a `MAKE_WEBHOOK_RESUMEN_URL` |
| `resumen` | Rutalytics Privado | Un `resumen_diario` de ejemplo, a `MAKE_WEBHOOK_RESUMEN_URL` |
| `credencial` | Rutalytics Privado | `credencial_usuario`, a `MAKE_WEBHOOK_CREDENCIALES_URL` |
| `restablecimiento` | Rutalytics Privado | `restablecimiento_contrasena`, a `MAKE_WEBHOOK_RESTABLECIMIENTO_URL` |

### Demo con los datos sembrados

Defina `MAKE_WEBHOOK_URL`, `SEMILLA_CORREO_CLIENTE` y, para recibir los avisos internos y
la ruta en correos reales, `SEMILLA_CORREO_ADMIN`, `SEMILLA_CORREO_GESTOR` y
`SEMILLA_CORREO_CONDUCTOR` en `.env` antes de sembrar. `seed.py` asigna ese correo a **Supermercado El Portal**, cuyo pedido
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

**Resumen diario**: con `MAKE_WEBHOOK_RESUMEN_URL` y `CORREO_OPERACIONES` definidos, pulse
**Enviar resumen del día** en el tablero, pídaselo a Gaby, o desde la terminal:

```bat
.venv\Scripts\flask.exe --app run enviar-resumen
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
   | `MAKE_WEBHOOK_URL` | El webhook del escenario SGDS (paso 5a) |
   | `MAKE_WEBHOOK_KEY` | La API key de los webhooks de Make (opcional, recomendada) |
   | `MAKE_WEBHOOK_RESUMEN_URL`, `MAKE_WEBHOOK_CREDENCIALES_URL`, `MAKE_WEBHOOK_RESTABLECIMIENTO_URL` | Las tres, el webhook de Rutalytics Privado (paso 5b). Sin la de credenciales, crear o restablecer cuentas por voz se rechaza |
   | `CORREO_OPERACIONES` | Destino de `stock_bajo`, `solicitud_contacto`, `accion_asistente` y el resumen diario |

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
   - a. [SGDS](#escenario-sgds-avisos-de-la-operación): un *Custom webhook* en
     `MAKE_WEBHOOK_URL` y un Router con una ruta por `tipo` (`pedido_estado`,
     `stock_bajo`, `solicitud_contacto`, `accion_asistente`), con la pestaña
     *Notificaciones* de la hoja "SGDS - Bitacora".
   - b. [Rutalytics Privado](#escenario-rutalytics-privado-resumen-credenciales-y-restablecimiento):
     otro *Custom webhook*, cuya URL va en las tres variables del escenario, con
     **"Data is confidential"** y un Router para `resumen_diario`, `credencial_usuario` y
     `restablecimiento_contrasena`.
   - c. [Programe el resumen diario](#resumen-diario-lo-envía-la-app) con el Programador
     de tareas (o el cron del servidor).
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

**2480 verificaciones en 28 suites contra MySQL 8.0, todas pasando, en unos cinco
minutos.** La salida resumida muestra por suite las verificaciones, el tiempo y solo las
líneas `FALLA`; `--detalle` muestra todo.

**La base de pruebas se borra en cada suite, y nunca es la del `.env`.** Las pruebas usan
`mysql+pymysql://sgds:sgds_clave@127.0.0.1:3307/logistica_pruebas` (otra base con la
variable `PRUEBAS_DATABASE_URL`) y se niegan a correr si el nombre de la base no termina
en `_pruebas`; el `DATABASE_URL` del `.env` no se usa. El ejecutor siembra una sola vez y
cada suite restaura esos datos al arrancar, por lo que los resultados son reproducibles;
corrida sola, la suite siembra por su cuenta.

**Ninguna suite contacta servicios externos.** `pruebas/_preparar.py`, que cada suite
importa primero, vacía `MAKE_WEBHOOK_URL`, `MAKE_WEBHOOK_CREDENCIALES_URL`,
`MAKE_WEBHOOK_RESTABLECIMIENTO_URL` y `RETELL_API_KEY` aunque el `.env` las defina, fija
una `CLAVE_CIFRADO` propia de las pruebas, y limita la red a la propia máquina: Make,
Retell y OSRM se simulan o caen a su respaldo. La única excepción es la parte de OSRM en
vivo de la suite 3, que solo corre con `--externas` (suma 10 verificaciones).

| Suite | Cubre | Pruebas |
|---|---|---|
| `prueba_01_acceso.py` | RF1 · autenticación, roles, tablero, zona horaria, config. de producción, inventario sembrado, lista blanca del correo en el login, contraseña visible solo mientras se presiona, botón Actualizar de las vistas de consulta | 107 |
| `prueba_02_pedidos_csv.py` | RF2 · alta manual, importación CSV, inventario, anulación | 64 |
| `prueba_03_ruteo.py` | RF3 · ordenamientos, respaldo offline (OSRM en vivo con `--externas`: 32) | 22 |
| `prueba_04_rutas_web.py` | RF3 · planificación, mapa, recálculo | 40 |
| `prueba_05_entrega_inventario.py` | RF4/RF5 · entrega, PoD, descuento de stock, concurrencia | 69 |
| `prueba_06_analitica_rendimiento.py` | RF6/RNF2 · indicadores, tiempos y zona horaria | 67 |
| `prueba_07_clientes_portal.py` | RF1/RF2 · normalización de clientes y portal | 73 |
| `prueba_08_administracion.py` | RF1 · administración de cuentas y clientes, lista blanca del correo en ambos formularios, política de contraseñas en el alta | 107 |
| `prueba_09_asistente.py` | RF4 · asistente de voz (firma, sesiones, aislamiento), avisos por Make, ruta finalizada del día y demo sembrada | 100 |
| `prueba_10_automatizaciones.py` | RF4/RF6 · avisos `pedido_estado` y `stock_bajo` (umbrales, CANCELADO y FALLIDO con motivo, API key), resumen diario (token, KPIs contra el tablero, fallidos, escape) | 79 |
| `prueba_11_asistente_roles.py` | RF1/RF4 · asistente por rol: agente y botón por rol, matriz de permisos, confirmación con estado, búsqueda para voz, funciones del gestor, del admin y del cliente, solicitudes de contacto, pedidos recientes y por fecha, migraciones | 217 |
| `prueba_12_sincronizacion_retell.py` | RF4 · configuración de los agentes (herramientas, prompts, documento) y `sincronizar-asistentes` contra un Retell simulado con el versionado real (agente y LLM en la misma versión, el 400 si no coinciden, segunda sincronización, borrador pendiente, `--rol`), timeout de 60 s con reintento, Talk While Waiting | 95 |
| `prueba_13_configuracion.py` | RF4/RF6 · configuración del negocio: catálogo de motivos de fallo con "Otro" y texto obligatorio, centro de distribución editable, uso en pantalla, voz y analítica, migración `migrar-configuracion` (en MySQL: clave ajena y coordenadas `FLOAT` → `DOUBLE`) | 59 |
| `prueba_14_reglas_pantalla.py` | RF1/RF2/RF3 · reglas de pantalla en servicios: NIT fijo, sedes, SKU, vehículos, edición de pedidos y bitácora de rutas | 62 |
| `prueba_15_auditoria.py` | RF1/RF4 · auditoría de solo agregar, pantalla *Bitácora*, quién originó cada evento de Make, `accion_asistente` (con `id_auditoria` y `fecha_hora`) y `credencial_usuario` por su webhook, auditoría de pantalla sin correo, endpoint incremental para Sheets | 92 |
| `prueba_16_cobertura_voz.py` | RF4 · cobertura por voz de cada ruta POST de la pantalla, exclusiones aprobadas, diálogo de formulario, cobertura cerrada | 385 |
| `prueba_17_voz_fase3.py` | RF1/RF4 · voz: clientes, sedes, productos, vehículos, rutas y edición de pedidos (gestor y admin); usuarios con credenciales por su webhook, configuración (incluido el tiempo de inactividad) y bitácora (admin); resúmenes, rechazos y auditoría | 104 |
| `prueba_18_voz_fase4.py` | RF1/RF4/RF5 · voz de la fase 4: el conductor inicia la ruta, entrega con nombre y documento y consulta su historial (auditoría sin correo); analítica y `atender-solicitud` del gestor; bitácora del admin; migración m007 y un pedido que falla, se reintenta y se entrega conservando cada prueba, con la observación dictada | 82 |
| `prueba_19_contrasenas.py` | RF1/RNF5 · apartado de contraseñas: acceso solo del admin, sin hash ni contraseña en la página, asignar, generar temporal, exigir cambio (uno y en bloque), cambio obligatorio en el perfil, cierre de sesiones abiertas, hash fuera de política, reglas por rol (cliente y otros administradores fuera de su alcance, la propia con la actual), migración `m008` | 153 |
| `prueba_20_bloqueo_acceso.py` | RNF5 · bloqueo tras 5 intentos fallidos en los cuatro roles: contador visible, un minuto de espera aun con la contraseña correcta, reinicio del contador, mismos mensajes para correos inexistentes, contraseña actual del perfil, columna en el apartado del admin, configuración, migración `m009` | 67 |
| `prueba_21_sesiones_errores.py` | RNF5 · bloqueo al quinto intento, política de contraseñas (la secuencia del 1 al 15 se rechaza), sesión única en dos navegadores (también la cookie de mantener sesión), cerrar sesión en una pestaña y navegar en otra, tiempo de inactividad configurable (reducir y aumentar, también con mantener sesión), cambio de correo desde el perfil, registro de errores (página 500 con referencia, pantalla, archivo, modo desarrollo), migración `m010` | 81 |
| `prueba_22_recuperacion.py` | RF1/RNF5 · *Olvidé mi contraseña*: la misma respuesta exista o no la cuenta, envío por su webhook con `URL_PUBLICA` (un `Host` falso no cambia el enlace), un enlace cada 5 minutos, política de contraseñas, cierre de sesiones y del bloqueo, enlace de un solo uso, alterado, caducado por otro cambio y vencido, cuentas inactivas, consola en desarrollo y pantalla sin canal | 44 |
| `prueba_23_cifrado.py` | RNF5 · datos personales: en la base la contraseña como hash y el documento y el teléfono cifrados (IV aleatorio), lectura en claro, índice ciego (unicidad en el formulario y en la base, búsqueda exacta), columnas cifradas que rechazan comparaciones SQL, auditoría enmascarada en pantalla y por voz, otra clave, migración `m011` sobre datos en claro | 46 |
| `prueba_24_archivos.py` | RNF5 · archivos subidos por su contenido: un ejecutable renombrado a `.pdf` y a `.csv` (Windows, Linux, macOS, script), PDF, comprimidos, imágenes, UTF-16 y binarios rechazados sin guardarse, cada rechazo en la bitácora, sin falsos positivos con CSV reales, límite de 2 MB y de 5 MB por petición (413) | 28 |
| `prueba_25_roles_permisos.py` | RF1/RNF5 · roles y permisos: reparto por defecto igual a los roles fijos, un rol nuevo asignado a dos cuentas con dos permisos propios distintos cada una, dos roles en una cuenta con la totalidad de sus permisos invertida, menú y botones según permisos, editar un rol del sistema (también por voz), reglas contra la escalada de privilegios, eliminar un rol, auditoría, migración `m012` | 59 |
| `prueba_26_voz_seguridad.py` | RF4/RNF5 · voz y seguridad: cada operación de voz con el permiso de su pantalla; roles, permisos de una cuenta, exigencia de cambio de contraseña, intentos de acceso y errores por voz (quitar un permiso lo quita por voz); la llamada atada a la sesión web (otro navegador, contraseña propia o restablecida, bloqueo por intentos, cuenta desactivada) y no cortada por inactividad (voz y latido); búsqueda por documento, teléfono y NIT; avisos y auditoría sin textos cifrados ni contraseñas; exclusiones con su motivo | 91 |
| `prueba_27_resumen_diario.py` | RF6 · resumen diario enviado por la app: el evento `resumen_diario` con los mismos campos que el GET de respaldo, solo al webhook privado con la API key; sin webhook o sin correo de operaciones no sale y se dice; quién lo originó (pantalla, voz, sistema); botón del tablero con su permiso y CSRF; operación de voz con confirmación; comando `enviar-resumen`; migración `m014` | 41 |
| `prueba_28_avisos_por_rol.py` | RF4 · avisos al correo de cada perfil: correo de avisos en *Usuarios*, *Mi perfil* y por voz (auditoría, validación, "ninguno"); `stock_bajo` y `solicitud_contacto` a los gestores, `accion_asistente` a los administradores, `resumen_diario` a ambos, credenciales al correo de inicio de sesión; inactivos y respaldo `CORREO_OPERACIONES`; `ruta_asignada` en cada operación, por voz y en pantalla, nunca de una ruta pasada; la siembra sin avisos y con los correos de avisos del `.env` | 46 |

La del asistente firma las peticiones con el propio SDK de Retell para probar la
verificación real; la de automatizaciones registra cada aviso a Make simulado con sus
encabezados, y la de sincronización usa un Retell simulado que valida cada llamada contra
la firma real del SDK. La de rutas en pantalla usa un OSRM simulado.

**`seed.py` no avisa a Make.** Aunque `MAKE_WEBHOOK_URL` esté definido en el `.env`, la
siembra corre dentro de `notificaciones.sin_avisos()` y no envía los avisos que generan
los datos de demostración. Los avisos empiezan con lo que se haga después en la
aplicación.

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

Las gráficas usan la paleta "Negro y amarillo" del logo: la serie principal (entregadas,
tasa de éxito, unidades que entran) va en `#2b2f38`, la segunda (cumplimiento de ventana,
unidades que salen) en `#e0ae14` y las fallidas en `#e0573f`. El rojo se reserva para
datos de fallo. **No se usa el par verde/rojo**: bajo daltonismo rojo-verde (cerca del 8 %
de los hombres) no se distingue. Las series de cada gráfica se separan por luminosidad,
que el daltonismo conserva: contraste 3.6:1 entre entregadas y fallidas y 6.5:1 entre las
dos líneas. El amarillo sobre blanco es tenue (2:1), así que el estado además se
identifica por leyenda y etiqueta, nunca por color solo, y cada gráfica ofrece una vista
en tabla.

## MySQL y MySQL Workbench (fase piloto)

**La migración está verificada:** las 18 tablas se crean correctamente y las 1925
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
.venv/bin/flask --app run migrar-contrasenas   # apartado de contraseñas y bloqueo (m008, m009)
.venv/bin/flask --app run migrar-seguridad     # sesión única, errores, cifrado, roles y voz (m010 a m013)
```

`migrar-configuracion` además pasa a `DOUBLE` las coordenadas del centro de distribución
si la tabla se creó con `FLOAT`: en MySQL, `FLOAT` es de precisión simple y guardaba
4.6482837 como 4.64828. Los valores ya redondeados no se recuperan; si necesita la
precisión completa, vuelva a guardar el centro en *Configuración*.

`migrar-contrasenas` agrega a `usuarios` las columnas `contrasena_actualizada_en`, `debe_cambiar_contrasena` y
`version_credencial`, y crea la tabla `intentos_acceso` del bloqueo por intentos fallidos.
Es idempotente. Las sesiones abiertas antes de aplicarla piden iniciar
sesión una vez más.

`migrar-seguridad` agrega a `usuarios` las columnas `token_sesion` y `sesion_actividad_en`,
a `configuracion_negocio` la columna `minutos_inactividad` (30 en las filas existentes) y
crea la tabla `errores_sistema` (m010). Después **cifra el documento y el teléfono** que
estén en claro (m011): amplía las columnas a `VARCHAR(300)`, agrega `documento_indice` con
su índice único y quita el índice único viejo sobre `documento`. Defina antes `CLAVE_CIFRADO` en el `.env` y
corra la migración con la misma que usará la aplicación: es la única que lee esos datos
(haga una copia de la base antes). Luego crea las tablas de roles y permisos con los
cuatro roles del sistema y su reparto de siempre (m012), y agrega a `sesiones_asistente`
la columna `huella_sesion` que ata cada llamada de voz a su sesión web (m013), da al
gestor el permiso de enviar el resumen (m014) y agrega `usuarios.correo_avisos` (m015). Es
idempotente. **Ejecútela antes de levantar esta
versión sobre una base existente**: sin las columnas nuevas, ninguna página carga.

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

**Producción: Azure.** La aplicación corre en Azure Container Apps con MySQL Flexible
Server, y se publica con el pipeline de Azure DevOps (`azure-pipelines.yml`: pruebas,
SAST, dependencias, imagen Docker y despliegue). Guía completa, arquitectura y variables:
[`docs/despliegue_azure.md`](docs/despliegue_azure.md). En producción las cuentas demo
**no** usan las contraseñas de la tabla de arriba: la siembra exige `SEMILLA_CLAVE_ADMIN`,
`SEMILLA_CLAVE_GESTOR`, `SEMILLA_CLAVE_CONDUCTOR` y `SEMILLA_CLAVE_CLIENTE`.

**Alternativa: Render.** `Procfile` y `render.yaml` están listos para Render o Railway, con gunicorn como servidor
WSGI y `/salud` como health check. Recuerde que el plan gratuito de Render suspende la
instancia tras 15 minutos sin tráfico y el siguiente acceso tarda ~30 segundos.

La aplicación es la misma en el PC y en el servidor: todo se configura con variables de
entorno. Al pasar a un servidor cambia esto:

| Qué | En el PC | En el servidor |
|---|---|---|
| `URL_PUBLICA` | La de ngrok, que cambia en cada sesión | La del servicio (`https://<su-servicio>.onrender.com` o su dominio), fija, **sin ngrok** |
| Agentes de Retell | `sincronizar-asistentes` cada vez que cambia ngrok | `flask --app run sincronizar-asistentes` **una vez después de fijar `URL_PUBLICA`** (en la *Shell* de Render, o desde el PC con la `URL_PUBLICA` del servidor), y de nuevo solo si vuelve a cambiar |
| Resumen diario | Programador de tareas de Windows | El **cron del proveedor** corre `flask --app run enviar-resumen` |
| `CLAVE_CIFRADO` | La del `.env` | **Fija**: `render.yaml` la genera una sola vez. Si migra una base existente, use la misma con la que se cifró; nunca la regenere |
| Variables de Make | Las del `.env` | **Las mismas**: `MAKE_WEBHOOK_URL` (SGDS), `MAKE_WEBHOOK_RESUMEN_URL`, `MAKE_WEBHOOK_CREDENCIALES_URL` y `MAKE_WEBHOOK_RESTABLECIMIENTO_URL` (Rutalytics Privado), `MAKE_WEBHOOK_KEY` y `CORREO_OPERACIONES` |

**Cron del resumen.** El horario de los cron va en UTC: las 18:00 de Bogotá son las
**23:00 UTC** (`0 23 * * *`). En Render es un servicio aparte de tipo *cron*, de pago (no
tiene plan gratuito), que comparte el código y las variables de la web: `render.yaml`
trae uno **comentado**; descoméntelo para crearlo. Debe tener la **misma** `CLAVE_CIFRADO`
y la misma `SECRET_KEY` que la web (el ejemplo las toma de ella con `fromService`), porque
en producción la aplicación no arranca sin ellas. En otro proveedor, programe el mismo
comando, `flask --app run enviar-resumen`, con su cron o su *scheduler* a las 23:00 UTC.
Si prefiere no pagar el cron, el resumen se puede seguir enviando desde el tablero o por
voz.
