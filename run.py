"""Punto de entrada de la aplicacion y comandos de administracion.

Uso:
    flask --app run init-db          Crea el esquema de la base de datos
    flask --app run sembrar          Carga datos de demostracion
    flask --app run migrar-clientes  Normaliza clientes en una base con datos
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5001)), debug=True)
