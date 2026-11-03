# Base de datos y seguridad de contraseñas — SGDS

Documento de referencia del Sistema de Gestión de Despachos (SGDS): ubicación de la base de datos, cómo se protegen las contraseñas de cada rol y cómo verificarlo en MySQL Workbench.

---

## 1. Ubicación de la base de datos en el proyecto

La base de datos se configura en tres archivos:

### 1.1 `config.py` — Configuración central

Está en la **raíz del proyecto**:

```
Proyecto Logistica/
├── config.py  ← Aquí está
├── .env
├── app/
├── instance/
└── ...
```

Define la base de datos según el entorno:

- **Desarrollo (líneas 36-40):** SQLite local
  ```python
  SQLALCHEMY_DATABASE_URI = "sqlite:///instance/logistica.db"
  ```
  Ubicación física: `instance/logistica.db`

- **Producción (líneas 43-46):** MySQL
  ```python
  SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", "")
  ```

### 1.2 `.env` — Variables de entorno

- **ENTORNO:** `desarrollo` o `produccion`
- **DATABASE_URL:** `mysql+pymysql://sgds:<clave>@127.0.0.1:3307/logistica`

Si `DATABASE_URL` está definida, tiene prioridad sobre el SQLite por defecto. Actualmente el proyecto está conectado a **MySQL en Docker** (`127.0.0.1:3307/logistica`).

### 1.3 `app/extensions.py` — Instancia de la base de datos

```python
db = SQLAlchemy()
```

### 1.4 Resumen por entorno

| Entorno | Base de datos | Ubicación |
|---------|--------------|-----------|
| **Desarrollo** | SQLite (o la definida en `DATABASE_URL`) | `instance/logistica.db` |
| **Producción** | MySQL | `mysql://usuario:clave@servidor/logistica` |
| **Pruebas** | SQLite en memoria | `:memory:` |

---

## 2. Protección de las contraseñas de los roles

Las contraseñas de los 4 roles ya se guardan protegidas con **hash**.

### 2.1 En el código

`app/models/usuario.py`, línea 21:

```python
METODO_HASH = "pbkdf2:sha256:600000"
```

Se usa **PBKDF2-SHA256 con 600.000 iteraciones y una sal aleatoria por usuario**, lo que recomienda OWASP.

- `establecer_contrasena()` (línea 69) guarda solo el hash.
- `verificar_contrasena()` (línea 72) compara lo que escribe el usuario con ese hash.

```python
def establecer_contrasena(self, contrasena):
    self.contrasena_hash = generate_password_hash(contrasena, method=METODO_HASH)

def verificar_contrasena(self, contrasena):
    return check_password_hash(self.contrasena_hash, contrasena)
```

Todos los lugares donde se asigna una contraseña pasan por esa función:

- Login y cambio de contraseña: `app/controllers/auth.py`
- Creación y restablecimiento de usuarios: `app/controllers/usuarios.py`
- Carga inicial de datos: `seed.py`

### 2.2 En la base de datos (MySQL)

Ningún usuario tiene la contraseña en texto plano:

| Usuario | Rol | Lo que hay guardado |
|---|---|---|
| admin@sgds.com | ADMIN | `pbkdf2:sha256:600000$aUgd…` |
| despachador@sgds.com | DESPACHADOR | `pbkdf2:sha256:600000$Nr9S…` |
| conductor1@sgds.com | CONDUCTOR | `pbkdf2:sha256:600000$MhPi…` |
| conductor2@sgds.com | CONDUCTOR | `pbkdf2:sha256:600000$9RhS…` |
| cliente@sgds.com | CLIENTE | `pbkdf2:sha256:600000$alZX…` |

---

## 3. Uso de MySQL Workbench

### 3.1 Activar la base de datos (Error 1046: No database selected)

El error **1046** significa que Workbench no sabe en qué base de datos ejecutar la consulta. Hay tres formas de activarla:

1. **Doble clic:** en el panel **SCHEMAS**, doble clic sobre `logistica`. Queda en **negrita** cuando está activa.
2. **Clic derecho:** sobre `logistica` → **Set as Default Schema**.
3. **En el script:**
   ```sql
   USE logistica;
   ```

Para dejarla activa siempre: pantalla de inicio de Workbench → clic derecho en la conexión **SGDS Local** → **Edit Connection** → campo **Default Schema** → escribir `logistica` → **Close**.

### 3.2 Error 1064 (error de sintaxis)

No es un problema de conexión con Docker. Si una consulta anterior funcionó (**1 row(s) affected**), la conexión está bien.

En este caso Workbench ejecutó `SE logistica` en lugar de `USE logistica;`: la selección empezó después de la "U".

Recomendación: poner el cursor dentro de una consulta y ejecutar solo esa sentencia con **⌘+Enter** (Mac) / **Ctrl+Enter** (Windows), o con el segundo rayo ⚡ de la barra.

---

## 4. Consultas para verificar las contraseñas por rol

```sql
USE logistica;

-- 1. Ver el hash guardado de cada usuario, por rol
SELECT id, correo, rol, contrasena_hash
FROM usuarios
ORDER BY rol, correo;

-- 2. Revisar el formato del hash de cada usuario
SELECT correo,
       rol,
       SUBSTRING_INDEX(contrasena_hash, '$', 1)            AS algoritmo,
       SUBSTRING_INDEX(SUBSTRING_INDEX(contrasena_hash, '$', 2), '$', -1) AS sal,
       LENGTH(contrasena_hash)                             AS longitud,
       CASE WHEN contrasena_hash LIKE 'pbkdf2:sha256:%'
            THEN 'PROTEGIDA' ELSE 'REVISAR' END            AS estado
FROM usuarios
ORDER BY rol;

-- 3. Resumen por rol: cuántas cuentas hay y cuántas están protegidas
SELECT rol,
       COUNT(*) AS total_usuarios,
       SUM(contrasena_hash LIKE 'pbkdf2:sha256:%') AS protegidas,
       SUM(contrasena_hash NOT LIKE 'pbkdf2:sha256:%') AS sin_proteger
FROM usuarios
GROUP BY rol;

-- 4. Comprobar que ningún hash se repite (efecto de la sal)
SELECT contrasena_hash, COUNT(*) AS repetidos
FROM usuarios
GROUP BY contrasena_hash
HAVING COUNT(*) > 1;
```

**Resultado esperado:**

- **Consulta 1:** textos largos del tipo `pbkdf2:sha256:600000$aUgd...$cd...`, no la contraseña real.
- **Consulta 2:** todos los usuarios como **PROTEGIDA**, longitud cercana a 100 caracteres y una **sal** distinta en cada uno.
- **Consulta 3:** `sin_proteger = 0` en los 4 roles (ADMIN, DESPACHADOR, CONDUCTOR, CLIENTE).
- **Consulta 4:** no devuelve filas, porque ningún hash se repite.

### 4.1 Documento y teléfono cifrados

El documento y el teléfono de cada cuenta se guardan **cifrados** con Fernet (AES-128 con
HMAC-SHA256). A diferencia del hash de la contraseña, el cifrado sí se puede revertir, pero
solo con la clave de la aplicación (`CLAVE_CIFRADO`, o `SECRET_KEY` si no se definió), que
no está en la base. La aplicación los muestra en claro; la base, no.

```sql
USE logistica;

-- 5. Documento y teléfono de cada usuario, tal como están en la base
SELECT correo, rol, documento, telefono, documento_indice
FROM usuarios
ORDER BY rol, correo;

-- 6. Cuentas con algún dato personal sin cifrar
SELECT COUNT(*) AS en_claro
FROM usuarios
WHERE (documento IS NOT NULL AND documento NOT LIKE 'gAAAAA%')
   OR (telefono  IS NOT NULL AND telefono  NOT LIKE 'gAAAAA%');
```

**Resultado esperado:**

- **Consulta 5:** `documento` y `telefono` son textos largos que empiezan por `gAAAAA`, no
  el número real. Cifrar dos veces el mismo dato da textos distintos. `documento_indice`
  es un código de 64 caracteres (HMAC-SHA256) con el que la aplicación comprueba que el
  documento no se repita, sin guardarlo en claro.
- **Consulta 6:** `en_claro = 0`.

En una base anterior a este cambio, `flask --app run migrar-seguridad` cifra los datos que
estén en claro.

---

## 5. ¿Qué es un hash y para qué sirve?

Un **hash** es una "huella digital" de un texto. Una función matemática toma cualquier dato (por ejemplo, una contraseña) y lo convierte en una cadena de longitud fija que parece aleatoria.

```
"admin123"   →  cd7a9f3e1b...   (siempre el mismo resultado)
"admin124"   →  51e0b88c4d...   (un cambio mínimo da algo totalmente distinto)
```

### 5.1 Sus tres propiedades clave

1. **No tiene reversa.** Del hash no se puede sacar la contraseña original, igual que de una huella digital no se puede reconstruir a la persona.
2. **Es determinista.** La misma entrada siempre produce el mismo hash, y eso es lo que permite verificar.
3. **Es sensible a cualquier cambio.** Una letra distinta produce un hash completamente diferente.

### 5.2 Para qué sirve en el SGDS

**Al crear o cambiar una contraseña** (`usuario.py:69`):
```
contraseña "admin123"  →  hash  →  se guarda SOLO el hash en MySQL
```

**Al iniciar sesión** (`usuario.py:72`):
```
lo que escribe el usuario  →  se le calcula el hash  →  ¿coincide con el guardado?
                                                        sí → entra / no → rechazado
```

El sistema **nunca necesita conocer la contraseña real**, solo comprueba que produce el mismo hash. Si alguien roba la base de datos, solo obtiene textos como `pbkdf2:sha256:600000$aUgd...` y no puede usarlos para entrar.

### 5.3 Partes del hash guardado en MySQL

```
pbkdf2:sha256:600000 $ aUgdLpqIIJNpqQq7 $ cd...
└──── algoritmo ────┘   └──── sal ─────┘   └ hash ┘
```

- **Algoritmo (PBKDF2-SHA256):** el método que se usa para calcular el hash.
- **600000:** el número de veces que se repite el cálculo. Así, probar cada contraseña cuesta tiempo y un atacante no puede probar millones por segundo.
- **Sal:** un valor aleatorio distinto para cada usuario. Gracias a ella, dos usuarios con la misma contraseña tienen hashes diferentes, y los atacantes no pueden usar tablas precalculadas de contraseñas comunes.

### 5.4 Hash vs. cifrado

| | **Hash** | **Cifrado** |
|---|---|---|
| ¿Se puede revertir? | No | Sí, con una llave |
| Uso típico | Contraseñas | Datos que luego se deben leer (tarjetas, documentos) |
| Si roban la base de datos | Solo obtienen hashes inútiles | Si roban también la llave, lo leen todo |

Por eso las contraseñas se protegen con **hash** y no con cifrado: el sistema no necesita volver a leerlas nunca.

---

## 6. ¿Cómo se creó el hash?

### 6.1 Quién lo pidió: `seed.py` (líneas 75-76)

Al poblar la base de datos, el script creó cada usuario (admin, despachador, conductores, cliente) y llamó:

```python
usuario = Usuario(nombre=nombre, correo=correo, rol=rol, activo=True)
usuario.establecer_contrasena(clave)
```

Lo mismo pasa cuando el administrador crea o restablece un usuario (`app/controllers/usuarios.py`), o cuando alguien cambia su contraseña (`app/controllers/auth.py`).

### 6.2 Qué función lo calcula: `usuario.py` (líneas 69-70)

```python
def establecer_contrasena(self, contrasena):
    self.contrasena_hash = generate_password_hash(contrasena, method="pbkdf2:sha256:600000")
```

`generate_password_hash` viene de la librería **Werkzeug** (parte de Flask) y hace tres pasos internamente:

```
Paso 1: genera una SAL aleatoria de 16 caracteres  →  "KuTbNlJm9HUjOBFg"

Paso 2: aplica PBKDF2 con SHA-256
        contraseña + sal → SHA-256 → SHA-256 → ... (600.000 veces) → 43b31f3e...

Paso 3: une todo en un solo texto con "$" como separador
        pbkdf2:sha256:600000 $ KuTbNlJm9HUjOBFg $ 43b31f3e2ee1...
```

Ese texto final es el que se guarda en la columna `contrasena_hash` de MySQL.

### 6.3 Demostración real

Ejecutada en el entorno del proyecto con la contraseña de ejemplo `Demo2026!`:

```
Hecho a mano   : pbkdf2:sha256:600000$_2Pw5EZK1YJGpuLR$c5909832a499...
Werkzeug (1ª)  : pbkdf2:sha256:600000$KuTbNlJm9HUjOBFg$43b31f3e2ee1...
Werkzeug (2ª)  : pbkdf2:sha256:600000$7Vcm7aDqaCkjJXap$6691437e6f27...
```

- **Misma contraseña, tres hashes distintos.** La sal cambia cada vez, y por eso los usuarios tienen hashes diferentes aunque compartan contraseña.
- **Verificación:** `Demo2026!` → **True**, pero `demo2026!` (con minúscula) → **False**. Un solo carácter distinto basta para rechazarla.

### 6.4 Cómo se verifica al iniciar sesión

`check_password_hash` lee la sal y las iteraciones del hash guardado, repite exactamente el mismo cálculo con lo que escribió el usuario y compara los resultados. Si coinciden, el usuario entra. En ningún momento se "descifra" nada.

---

## 7. El proceso completo, paso a paso

### 7.1 Qué es la sal

La sal **no** es la combinación del algoritmo con la contraseña. Es simplemente un **valor aleatorio** (por ejemplo `KuTbNlJm9HUjOBFg`) que el sistema genera al crear la contraseña. No depende ni de la contraseña ni del algoritmo; es un ingrediente extra que se mezcla con la contraseña.

```
Ingredientes:   contraseña  +  sal (aleatoria)
Receta:         algoritmo PBKDF2-SHA256, repetido 600.000 veces
Resultado:      el hash
```

### 7.2 Resumen del proceso

1. **Al crear la contraseña**
   - Se genera una **sal aleatoria**.
   - Se combina **contraseña + sal** y se pasa por el algoritmo **600.000 veces**.
   - Se guarda en MySQL: `algoritmo $ sal $ resultado`.

2. **Al iniciar sesión**
   - El usuario escribe su contraseña.
   - El sistema lee de MySQL la **sal** y el número de iteraciones de ese usuario.
   - Repite el cálculo: **contraseña escrita + misma sal**, 600.000 veces.
   - **Compara el hash nuevo con el hash guardado.** Si coinciden, entra; si no, acceso denegado.

### 7.3 La comparación es hash contra hash

La comparación **no** es entre el resultado y la contraseña que digita el usuario:

```
hash guardado en MySQL   vs   hash recién calculado con lo que escribió el usuario
```

La contraseña real nunca se guarda ni se compara directamente. Solo se comparan los dos resultados del cálculo.

---

## 8. ¿Por qué este algoritmo?

### 8.1 Es un algoritmo estándar

| Pieza | Estándar | Quién lo define |
|---|---|---|
| **PBKDF2** | RFC 8018 (PKCS #5) y NIST SP 800-132 | IETF y NIST (instituto de estándares de EE. UU.) |
| **SHA-256** | FIPS 180-4 | NIST |
| **600.000 iteraciones** | Recomendación actual para PBKDF2-SHA256 | OWASP |

Por ser público y estándar, cualquier lenguaje lo implementa igual. El mismo hash calculado en Python, Java o PHP da el mismo resultado. La seguridad no depende de que el algoritmo sea secreto, sino de que la contraseña no se conoce y el cálculo es lento.

### 8.2 De dónde sale cada ingrediente

| Ingrediente | Quién lo pone | Cuándo |
|---|---|---|
| **Contraseña** | El usuario (o el admin) la escribe a mano | Al crear la cuenta o al iniciar sesión |
| **Sal** | El sistema la genera sola, aleatoria | Una sola vez, al crear o cambiar la contraseña |
| **Algoritmo** | Está fijo en el código (`usuario.py:21`) | Siempre el mismo |

La sal se genera automáticamente para que, aunque dos personas escriban la misma contraseña, sus hashes sean distintos. Queda guardada junto al hash para poder repetir el cálculo al iniciar sesión.

### 8.3 Razones para usar PBKDF2-SHA256

- **Es lento a propósito.** Las 600.000 repeticiones hacen que cada intento tarde unos milisegundos. Para un usuario legítimo no se nota, pero un atacante que quiera probar millones de contraseñas tardaría años.
- **Viene incluido en Flask (Werkzeug).** No hace falta instalar librerías extra.
- **Hubo una razón técnica en el proyecto.** El comentario en `usuario.py` (líneas 17-21) lo explica: Werkzeug usa por defecto **scrypt**, pero el Python de macOS no lo trae disponible, así que se eligió PBKDF2, que funciona en todos lados.

Hay alternativas más modernas: **Argon2id**, que es la primera recomendación de OWASP hoy, **scrypt** y **bcrypt**. PBKDF2 con 600.000 iteraciones sigue siendo seguro y aceptado.
