# Informe de seguridad: análisis estático, de dependencias y pruebas de penetración

Proyecto: Fliyt — Sistema de Gestión de Despachos (SGDS)

Electiva Desarrollo Web — Ingeniería de Sistemas, Universidad de San Buenaventura

Equipo: Yorman Andrés Suárez Monroy, Juan Andrés Santoya Rojas

Fecha: 3 de noviembre de 2026

Rúbrica: cubre la sección 11 (Seguridad: análisis estático y dinámico) y la sección 12 (Pruebas de penetración y corrección).


## 1. Alcance y autorización

Las pruebas se realizaron exclusivamente sobre la aplicación Fliyt, desarrollada por el equipo, sobre la cual se tiene plena autorización. Se probaron dos entornos:

- Entorno local de desarrollo: la aplicación ejecutándose con el servidor de Flask (Werkzeug) en http://192.168.56.1:5001, alcanzada desde una máquina Kali Linux en VirtualBox (red host-only). Aquí se concentraron las pruebas activas (escaneo de vulnerabilidades).
- Entorno de producción: la aplicación desplegada en Azure Container Apps (https://rutalytics.yellowplant-1f14b776.eastus2.azurecontainerapps.io), con gunicorn tras el proxy HTTPS de Azure. Sobre producción solo se contemplaron verificaciones pasivas, por las reglas de Microsoft que prohíben pruebas de denegación de servicio o tráfico intensivo contra sus servicios.
Se respetaron las reglas de compromiso de Microsoft para pruebas en Azure: no se ejecutaron ataques de denegación de servicio, ni pruebas contra servicios de terceros (Retell, Make) ni contra la infraestructura de Azure.


## 2. Herramientas utilizadas


| Herramienta | Tipo | Uso |
| --- | --- | --- |
| Bandit | SAST (análisis estático) | Revisión del código fuente Python en busca de patrones inseguros |
| pip-audit | SCA (dependencias) | Detección de bibliotecas con vulnerabilidades conocidas |
| nmap (--script vuln) | Reconocimiento / vulnerabilidades | Puertos, versión del servicio y CVEs conocidos |
| WhatWeb | Reconocimiento | Identificación de tecnologías del servidor web |
| Nikto | Escaneo web | Configuración, cabeceras y archivos expuestos |
| OWASP ZAP 2.17 | DAST (análisis dinámico) | Escaneo activo de vulnerabilidades web (OWASP) |
| Suite de pruebas propia | Verificación | 30 suites automatizadas que validan los controles de seguridad |


## 3. Resumen ejecutivo

No se identificaron vulnerabilidades de severidad alta ni crítica. Los hallazgos relevantes fueron configuraciones de seguridad (cabeceras HTTP ausentes, cookie sin SameSite, fuga de versión del servidor) y dependencias desactualizadas. Todos los hallazgos accionables fueron corregidos y se verificaron de nuevo. Los hallazgos restantes corresponden al servidor de desarrollo y no aplican al entorno de producción.


| Severidad | Detectados | Corregidos | Aceptados / no aplican a producción |
| --- | --- | --- | --- |
| Crítica / Alta | 0 | 0 | 0 |
| Media | 3 | 3 | 0 |
| Baja | 4 | 4 | 0 |
| Informativa | 3 | 1 | 2 |


## 4. Sección 11 — Análisis estático (SAST) y de dependencias (SCA)

Estos análisis se ejecutan además de forma automática en el pipeline de Azure DevOps (etapa Seguridad), y sus reportes quedan como artefactos en cada ejecución.


### 4.1 Análisis estático del código (Bandit)

Bandit analizó 14 494 líneas de código Python (carpeta app, run.py, config.py, seed.py).


| Severidad | Regla | Descripción | Evaluación |
| --- | --- | --- | --- |
| Media | B104 | Enlace a todas las interfaces (0.0.0.0) en run.py | No aplica a producción: es el servidor de desarrollo. En producción se usa gunicorn. |
| Baja | B105 | Posible contraseña fija 'None' | Falso positivo (comparación, no una credencial). |
| Baja | B110 (x3) | try/except/pass | Deliberado: la telemetría y los avisos nunca deben tumbar una petición. |
| Baja | B311 | Generador pseudoaleatorio no apto para seguridad | No aplica a credenciales: las contraseñas usan el módulo secrets; B311 está en la generación de datos de demostración. |

Resultado: 0 hallazgos de severidad alta. El pipeline está configurado para fallar solo ante hallazgos de severidad alta.


### 4.2 Análisis de dependencias (pip-audit)

La primera ejecución detectó varias dependencias con vulnerabilidades conocidas. Se actualizaron y la verificación posterior quedó limpia.


| Dependencia | Versión vulnerable | Versión corregida | Estado |
| --- | --- | --- | --- |
| urllib3 | 1.26.20 | 2.8.0 | Corregida |
| Werkzeug | 3.1.8 | 3.1.9 | Corregida |
| requests | 2.32.5 | 2.33.0 | Corregida |
| certifi / dnspython / python-dotenv | varias | últimas | Corregidas |

Verificación posterior (pip-audit sobre requirements.txt): «No known vulnerabilities found».

Clasificación OWASP: A06:2021 — Componentes vulnerables y desactualizados.


### 4.3 Protección de datos sensibles (cifrado)

El documento y el teléfono de cada cuenta se almacenan cifrados con Fernet (AES-128 en modo CBC con HMAC), con una clave dedicada (CLAVE_CIFRADO) derivada mediante HKDF-SHA256. La búsqueda por documento usa un índice ciego (HMAC), de modo que el valor en claro nunca se guarda. La suite 23 verifica este comportamiento.


## 5. Sección 12 — Pruebas de penetración


### 5.1 Reconocimiento (nmap y WhatWeb)

nmap -sV -O --script vuln sobre el puerto 5001:

- Servicio: Werkzeug httpd 3.1.8 (Python 3.14.4) — servidor de desarrollo.
- Scripts de XSS almacenado, XSS basado en DOM y CSRF: no encontraron vulnerabilidades.
- http-slowloris: marcó el servidor como «probablemente vulnerable» a denegación de servicio (CVE-2007-6750). Es un rasgo del servidor de desarrollo de un solo proceso; no se probó de forma activa (prohibido por las reglas de Azure).
- vulners: listó CVEs asociados a Python 3.14.4. Corresponden al intérprete de la máquina local; la imagen de producción se construye sobre python:3.13-slim y se actualiza al reconstruir.
WhatWeb confirmó la tecnología (Flask/Werkzeug, Python) y, antes de la corrección, la exposición de la versión en la cabecera Server.


### 5.2 Escaneo de vulnerabilidades (Nikto y OWASP ZAP)

Nikto y ZAP coincidieron en los mismos hallazgos, todos relacionados con la configuración de seguridad. Resultado de ZAP 2.17: 0 altas, 2 medias, 3 bajas, 3 informativas.


| Hallazgo | Severidad | OWASP 2021 | Herramienta |
| --- | --- | --- | --- |
| Content-Security-Policy ausente | Media | A05 Configuración de seguridad | ZAP, Nikto |
| Falta protección anti-clickjacking (X-Frame-Options) | Media | A05 | ZAP, Nikto |
| Cookie de sesión sin atributo SameSite | Baja | A05 / A01 | ZAP |
| Servidor revela versión en la cabecera Server | Baja | A05 | ZAP, WhatWeb |
| X-Content-Type-Options ausente | Baja | A05 | ZAP, Nikto |
| Faltan Referrer-Policy y Permissions-Policy | Baja | A05 | Nikto |
| User Controllable HTML Element Attribute (posible XSS) | Informativa | A03 Inyección | ZAP |
| Session Management Response Identified | Informativa | — | ZAP |
| User Agent Fuzzer | Informativa | — | ZAP |


### 5.3 Verificación de controles de acceso (pruebas dirigidas y suite automatizada)

Los controles de acceso y autenticación se verifican de forma repetible con la suite de pruebas automatizada, que forma parte del pipeline:

- Bloqueo por intentos fallidos (suite 20): tras 5 contraseñas erradas, la cuenta queda bloqueada 60 s.
- Roles y permisos (suite 25): cada pantalla y acción exige el permiso correspondiente; un rol sin permiso recibe 403.
- Sesión única e inactividad (suite 21): una nueva sesión invalida la anterior; la inactividad cierra la sesión.
- Recuperación de contraseña (suite 22): enlace de un solo uso, válido 30 minutos, sin filtrar el token por la cabecera Referer.
- Cifrado de datos personales (suite 23) y contenido real de archivos subidos (suite 24): un ejecutable renombrado a .csv es rechazado.

## 6. Correcciones aplicadas y nueva verificación

Cada hallazgo accionable se corrigió en el código y se verificó de nuevo. Las correcciones están en el repositorio y se despliegan por el pipeline. Se añadió además la suite 30, que comprueba las cabeceras de seguridad para evitar regresiones.


| Hallazgo | Corrección aplicada | Verificación posterior |
| --- | --- | --- |
| CSP ausente y clickjacking | Content-Security-Policy con frame-ancestors 'self', más X-Frame-Options: DENY (app/__init__.py). | curl muestra ambas cabeceras; suite 30 en verde; sin violaciones de CSP en el navegador. |
| X-Content-Type-Options ausente | Cabecera X-Content-Type-Options: nosniff en cada respuesta. | curl y suite 30 confirman la cabecera. |
| Referrer-Policy / Permissions-Policy | Se añaden ambas; la página de restablecer conserva no-referrer. | suite 30 y suite 22 en verde. |
| Cookie sin SameSite | SESSION_COOKIE_SAMESITE = 'Lax' (config.py); se mantiene HttpOnly y Secure en producción. | Set-Cookie incluye SameSite=Lax; suite 30 lo verifica. |
| Servidor revela versión | La cabecera Server se fija en 'Fliyt' (sin versión de Werkzeug/Python). | curl ya no muestra la versión. |
| Dependencias vulnerables | Actualización de urllib3, Werkzeug, requests y otras. | pip-audit: sin vulnerabilidades conocidas. |
| HSTS | Strict-Transport-Security cuando la conexión es HTTPS (producción tras el proxy de Azure). | suite 30 confirma HSTS sobre HTTPS y su ausencia en HTTP. |
| Posible XSS (informativo) | Revisado: Jinja2 autoescapa todas las variables; no se reprodujo XSS. La CSP añade una segunda capa. | Sin reproducción; cubierto por la CSP. |

Evidencia de las cabeceras tras la corrección (petición a /auth/login):

```
Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net ...
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: strict-origin-when-cross-origin
Permissions-Policy: geolocation=(self), microphone=(self), camera=(), payment=()
Server: Fliyt
Set-Cookie: session=...; HttpOnly; Path=/; SameSite=Lax
```

## 7. Hallazgos aceptados o no aplicables a producción


| Hallazgo | Motivo |
| --- | --- |
| Slowloris / DoS (nmap) | Propio del servidor de desarrollo de un solo proceso. En producción corre gunicorn con varios hilos tras el proxy de Azure Container Apps, que absorbe este tipo de carga. No se probó de forma activa por las reglas de Azure. |
| CVEs de Python 3.14.4 (nmap/vulners) | Son del intérprete de la máquina local de pruebas. La imagen de producción se construye sobre python:3.13-slim y se actualiza al reconstruir la imagen. |
| B104 — enlace a 0.0.0.0 (Bandit) | Corresponde al arranque del servidor de desarrollo (run.py). En producción el enlace lo gestiona gunicorn dentro del contenedor. |
| User Agent Fuzzer, Session Management (ZAP, informativos) | Informativos; no representan una vulnerabilidad. La gestión de sesión ya aplica sesión única, inactividad y cookie endurecida. |


## 8. Conclusión

La aplicación Fliyt no presentó vulnerabilidades de severidad alta. Los hallazgos de configuración detectados por el análisis dinámico (cabeceras, cookie, versión del servidor) y las dependencias desactualizadas fueron corregidos y verificados de nuevo, y quedan protegidos por pruebas automatizadas en el pipeline. El ciclo completo —detección, priorización, corrección y nueva verificación— se cumplió para todos los hallazgos accionables. Como trabajo futuro se plantea endurecer la CSP eliminando 'unsafe-inline' mediante nonces en los scripts en línea.

