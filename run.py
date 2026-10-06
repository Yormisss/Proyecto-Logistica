"""Punto de entrada de la aplicacion y comandos de administracion.

Uso:
    flask --app run init-db          Crea el esquema de la base de datos
    flask --app run sembrar          Carga datos de demostracion
    flask --app run migrar-clientes  Normaliza clientes en una base con datos
    flask --app run migrar-asistente Crea o actualiza las tablas del asistente de voz
    flask --app run sincronizar-asistentes  Crea o actualiza los agentes en Retell
    flask --app run documentar-asistentes   Regenera docs/configuracion_retell.md
    python run.py                    Levanta el servidor de desarrollo
"""

import os

import click

from app import crear_app
from app.extensions import db

app = crear_app(os.getenv("ENTORNO", "desarrollo"))


@app.shell_context_processor
def contexto_shell():
    from app import models

    return {"db": db, "models": models}


@app.cli.command("init-db")
def init_db():
    """Crea todas las tablas definidas en la capa de modelos."""
    db.create_all()
    click.echo("Esquema de base de datos creado.")


@app.cli.command("reset-db")
def reset_db():
    """Elimina y vuelve a crear el esquema (solo desarrollo)."""
    db.drop_all()
    db.create_all()
    click.echo("Base de datos reiniciada.")


@app.cli.command("sembrar")
def sembrar():
    """Carga usuarios, productos y pedidos de demostracion."""
    from seed import sembrar_datos

    sembrar_datos()


@app.cli.command("migrar-clientes")
def migrar_clientes():
    """Crea las tablas de clientes y deriva los existentes desde los pedidos.

    Pensado para una base que ya tiene operacion cargada: `init-db` agrega tablas
    nuevas pero no columnas nuevas, y aqui `pedidos` gana dos claves ajenas.
    """
    from migraciones.m001_clientes_y_direcciones import aplicar

    aplicar()


@app.cli.command("migrar-asistente")
def migrar_asistente():
    """Crea o actualiza las tablas del asistente de voz en una base existente."""
    from migraciones import (
        m002_sesiones_asistente, m003_confirmacion_asistente, m004_solicitudes_contacto,
    )

    m002_sesiones_asistente.aplicar()
    m003_confirmacion_asistente.aplicar()
    m004_solicitudes_contacto.aplicar()


@app.cli.command("sincronizar-asistentes")
@click.option("--rol", "roles", multiple=True,
              type=click.Choice(["conductor", "gestor", "admin", "cliente"]),
              help="Solo este rol; se puede repetir. Sin la opcion, los cuatro.")
def sincronizar_asistentes(roles):
    """Crea o actualiza en Retell el agente de voz de cada rol y lo publica.

    Requiere RETELL_API_KEY y URL_PUBLICA (la URL https de ngrok o Render). Al
    cambiar la URL de ngrok basta con volver a ejecutarlo.
    """
    from app.asistentes.configuracion import normalizar_url_publica
    from app.asistentes.sincronizacion import TIMEOUT_SINCRONIZACION
    from app.asistentes.sincronizacion import sincronizar_asistentes as sincronizar
    from app.services.asistente import cliente_retell

    if not app.config.get("RETELL_API_KEY"):
        raise click.ClickException("Defina RETELL_API_KEY para sincronizar los asistentes.")
    try:
        url = normalizar_url_publica(app.config.get("URL_PUBLICA"))
    except ValueError as error:
        raise click.ClickException(str(error))

    click.echo(f"Sincronizando los asistentes con {url}")
    with app.app_context():
        resultados = sincronizar(cliente_retell(timeout=TIMEOUT_SINCRONIZACION), url, app.config,
                                 roles=roles)

    faltantes = []
    for r in resultados:
        clave = r.definicion.clave
        reintento = (f", tras {r.reintentos} reintento{'s' if r.reintentos > 1 else ''} por timeout"
                     if r.reintentos else "")
        if r.error:
            click.echo(f"  {clave}: ERROR{reintento}, {r.error}")
            continue
        click.echo(f"  {clave}: {r.accion} {r.agent_id}, versión {r.version} publicada "
                   f"({r.funciones} funciones{reintento})")
        if r.falta_en_env:
            faltantes.append(f"{r.definicion.variable}={r.agent_id}")

    if faltantes:
        click.echo("\nAgregue al .env (y a las variables del despliegue):")
        for linea in faltantes:
            click.echo(linea)
    if any(r.error for r in resultados):
        raise SystemExit(1)


@app.cli.command("documentar-asistentes")
def documentar_asistentes():
    """Regenera docs/configuracion_retell.md con la configuracion de cada agente."""
    from pathlib import Path

    from app.asistentes.configuracion import generar_documento

    destino = Path(__file__).resolve().parent / "docs" / "configuracion_retell.md"
    destino.parent.mkdir(exist_ok=True)
    destino.write_text(generar_documento(), encoding="utf-8")
    click.echo(f"Escrito {destino}")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5001)), debug=True)
