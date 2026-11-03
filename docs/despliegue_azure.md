# Despliegue en Azure con Azure DevOps

Rutalytics (SGDS) corre en **Azure Container Apps** con **Azure Database for MySQL
Flexible Server**. Cada push a `main` en **Azure Repos** dispara el pipeline de
**Azure DevOps**: pruebas → seguridad → imagen Docker → despliegue.

## Arquitectura

```
 PC del equipo (agente propio "Default")            Azure (rg-rutalytics)
 ┌───────────────────────────────┐   docker push   ┌──────────────────────────────┐
 │ Azure Pipelines               │ ──────────────▶ │ Container Registry (ACR)     │
 │  1. Pruebas (29 suites, MySQL)│                 └──────────────┬───────────────┘
 │  2. Bandit + pip-audit        │   az containerapp update       │ imagen
 │  3. docker build              │ ──────────────▶ ┌──────────────▼───────────────┐
 │  4. desplegar.ps1             │                 │ Container App "rutalytics"   │◀── usuarios (HTTPS)
 └───────────────────────────────┘                 │  gunicorn + Flask (puerto 8000)│◀── Retell (funciones de Gaby)
        ▲ git push                                 │ Job "rutalytics-resumen"     │──▶ Make (webhooks)
 Azure Repos (fliyt/Rutalytics) ◀── espejo ── GitLab└──────────────┬───────────────┘
                                                                  │ TLS
                                                   ┌──────────────▼───────────────┐
                                                   │ MySQL Flexible Server B1ms   │
                                                   └──────────────────────────────┘
```

| Pieza | Servicio | Por qué |
|---|---|---|
| Aplicación | Azure Container Apps (0,5 vCPU, 1 GiB, 1 réplica fija) | HTTPS incluido, URL fija para Retell (sin ngrok), réplica siempre encendida para que la voz no espere un arranque en frío |
| Base de datos | MySQL Flexible Server Burstable B1ms, 32 GB | Gratis 12 meses en la cuenta gratuita; TLS obligatorio (ver `opciones_ssl_mysql` en `config.py`) |
| Imágenes | Azure Container Registry Standard | Gratis 12 meses |
| Resumen diario | Container Apps Job con cron `0 23 * * *` (18:00 Bogotá) | Corre `flask --app run enviar-resumen` con la misma imagen |
| CI/CD | Azure Pipelines, agente propio | La organización nueva no tiene agentes hospedados gratuitos sin aprobación, y la cuenta gratuita no permite ACR Tasks |
| Secretos | Grupo de variables `rutalytics-produccion` (Library), secretos de la Container App | Nunca en el repositorio ni en la imagen (`.gitignore`, `.dockerignore`) |

## Puesta en marcha (una sola vez)

1. **Código en Azure Repos** (CMD, en `C:\Proyecto-Logistica`):
   ```
   git remote add azure https://dev.azure.com/fliyt/Rutalytics/_git/Rutalytics
   git push azure main
   ```
2. **Recursos de Azure** (portal.azure.com → Cloud Shell → Bash):
   ```
   git clone https://fliyt:<PAT>@dev.azure.com/fliyt/Rutalytics/_git/Rutalytics rutalytics
   cd rutalytics && bash infra/azure/01_recursos.sh
   ```
   Guarda las claves en `~/rutalytics/estado.env` (persistente en Cloud Shell) y
   muestra la URL pública y las contraseñas de las cuentas demo.
3. **Azure DevOps**: suba el `.env` del proyecto a Cloud Shell (botón *Manage files →
   Upload*) y muévalo a `~/rutalytics/.env`; después:
   ```
   export AZURE_DEVOPS_EXT_PAT=<PAT>
   bash infra/azure/02_devops.sh fliyt
   bash infra/azure/03_backlog.sh fliyt
   ```
4. **Agente**: *Project settings → Agent pools → Default → New agent → Windows*. Descomprima
   en `C:\agente`, `config.cmd` con la URL `https://dev.azure.com/fliyt` y el PAT, y deje
   corriendo `run.cmd`. Requiere Python 3, Docker Desktop (contenedor `sgds-mysql`) y Azure CLI.
5. **Pipeline**: *Pipelines → Rutalytics CI-CD → Run*. La primera vez, *Permit* al grupo de
   variables, la conexión `azure-rutalytics` y el entorno `produccion`.
6. **Retell**: en el `.env` del PC ponga `URL_PUBLICA=<URL de Azure>` y corra
   `.venv\Scripts\flask.exe --app run sincronizar-asistentes`. Ya no hace falta ngrok.

## Variables

Las de configuración van como variables normales del grupo; las marcadas 🔒 como
**secretas** (no se muestran en la interfaz ni en los registros):

| Variable | Origen |
|---|---|
| `GRUPO`, `APP`, `JOB`, `ACR`, `ACR_SERVIDOR`, `ENTORNO_CA`, `URL_PUBLICA`, `CONEXION_AZURE` | `01_recursos.sh` |
| 🔒 `DATABASE_URL`, `FLASK_SECRET_KEY` (la `SECRET_KEY` de Flask: Azure Pipelines reserva el prefijo `secret`), `CLAVE_CIFRADO`, `AUTOMATIZACION_TOKEN` | Generadas una vez por `01_recursos.sh`. **`CLAVE_CIFRADO` no se cambia nunca** |
| 🔒 `SEMILLA_CLAVE_ADMIN/GESTOR/CONDUCTOR/CLIENTE` | Generadas: en producción las contraseñas del README no sirven |
| 🔒 `RETELL_API_KEY`, `MAKE_WEBHOOK_*` | El `.env` del proyecto |
| `RETELL_AGENTE_*_ID`, `RETELL_VOZ_ID`, `CORREO_OPERACIONES`, `SEMILLA_CORREO_*` | El `.env` del proyecto |

## Evidencias para la rúbrica

| Sección | Dónde se ve |
|---|---|
| 1 y 8. Planeación, Boards | *Boards → Backlogs / Sprints*: Features RF1–RNF5, PBIs con criterios de aceptación, Bugs corregidos, 4 sprints |
| 7. Control de versiones | Azure Repos (y espejo en GitLab), ramas `integracion-juan` y `despliegue-azure`, commits por bloque |
| 8. CI/CD | *Pipelines → Rutalytics CI-CD*: 4 etapas; entorno `produccion` con historial de despliegues |
| 9. Pruebas funcionales | Artefacto `evidencia-pruebas` de cada ejecución (29 suites, ~2500 verificaciones) |
| 11. SAST / SCA | Artefacto `evidencia-seguridad`: `sast_bandit.txt/.json`, `sca_pip_audit.txt` |
| 13. Despliegue | URL pública `https://rutalytics.<...>.azurecontainerapps.io`, `/salud` |

## Operación

- **Registros**: portal → Container App `rutalytics` → *Log stream*.
- **Correr el resumen ya**: `az containerapp job start -n rutalytics-resumen -g rg-rutalytics`.
- **Volver a una versión anterior**: *Revisions and replicas* → activar la revisión previa, o
  re-ejecutar el pipeline de un build anterior.
- **Costo**: MySQL B1ms y ACR Standard entran en los servicios gratis de 12 meses; la
  Container App con 1 réplica consume del crédito. El crédito de US$200 de la cuenta
  gratuita dura 30 días; después hay que pasar a pago por uso o los recursos se detienen.
