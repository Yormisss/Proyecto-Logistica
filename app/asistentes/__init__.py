"""Configuracion de los agentes de Retell, guardada en el repositorio.

- `prompts/`: prompt y mensaje de bienvenida de cada rol, y las reglas comunes.
- `configuracion.py`: arma la configuracion completa de cada agente; las
  funciones salen del registro de `app.controllers.asistente_api`.
- `sincronizacion.py`: crea o actualiza los agentes en Retell
  (`flask --app run sincronizar-asistentes`).
"""
