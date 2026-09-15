"""Application factory.

Centraliza el registro de extensiones, modelos y controladores bajo el patron
Modelo-Vista-Controlador exigido por el RNF3. Las plantillas viven en `app/views`
para que la estructura de carpetas refleje explicitamente el patron.
"""

import time
from datetime import datetime

from flask import Flask, g, render_template, request

from config import CONFIGURACIONES
from app.extensions import csrf, db, login_manager


def crear_app(nombre_configuracion="desarrollo"):
    app = Flask(
        __name__,
        template_folder="views",
        static_folder="static",
    )
    app.config.from_object(CONFIGURACIONES[nombre_configuracion])

    _registrar_extensiones(app)
    _registrar_controladores(app)
    _registrar_errores(app)
    _registrar_contexto(app)
    _registrar_medicion(app)

    return app


# Cada cuantas peticiones se purgan las mediciones antiguas.
_INTERVALO_PURGA = 200
_LIMITE_MEDICIONES = 5000
_contador_peticiones = {"valor": 0}


def _registrar_medicion(app):
    """RNF2 - Instrumenta el tiempo de respuesta de cada peticion.

    La medicion se toma en el servidor, por lo que no incluye la latencia de la
    red movil; el requisito la contempla aparte ("sujeto a la conexion a internet
    movil").
    """

    @app.before_request
    def iniciar_cronometro():
        g._inicio_peticion = time.perf_counter()

    @app.after_request
    def registrar_duracion(respuesta):
        inicio = g.pop("_inicio_peticion", None)
        if inicio is None or request.endpoint in (None, "static"):
            return respuesta

        duracion_ms = (time.perf_counter() - inicio) * 1000

        # La cabecera permite verificar el tiempo desde el navegador.
        respuesta.headers["X-Tiempo-Respuesta-ms"] = f"{duracion_ms:.1f}"

        try:
            from app.models import MedicionRendimiento

            # La medicion se escribe en su propia transaccion, no en la sesion
            # de la peticion: un commit aqui confirmaria cambios que la vista
            # pudo haber dejado pendientes deliberadamente.
            with db.engine.begin() as conexion:
                conexion.execute(
                    MedicionRendimiento.__table__.insert().values(
                        endpoint=request.endpoint,
                        metodo=request.method,
                        estado_http=respuesta.status_code,
                        duracion_ms=duracion_ms,
                        registrado_en=datetime.utcnow(),
                    )
                )

                _contador_peticiones["valor"] += 1
                if _contador_peticiones["valor"] % _INTERVALO_PURGA == 0:
                    _purgar_mediciones(conexion)
        except Exception:
            # La telemetria nunca debe tumbar una peticion de la operacion.
            pass

        return respuesta


def _purgar_mediciones(conexion):
    """Conserva solo las mediciones mas recientes para no inflar la base."""
    from sqlalchemy import select

    from app.models import MedicionRendimiento

    tabla = MedicionRendimiento.__table__

    corte = conexion.execute(
        select(tabla.c.id)
        .order_by(tabla.c.id.desc())
        .offset(_LIMITE_MEDICIONES)
        .limit(1)
    ).scalar()

    if corte:
        conexion.execute(tabla.delete().where(tabla.c.id <= corte))


def _registrar_extensiones(app):
    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    # Importacion diferida: registra los modelos en el metadata de SQLAlchemy.
    with app.app_context():
        from app import models  # noqa: F401


def _registrar_controladores(app):
    from app.controllers.auth import auth_bp
    from app.controllers.admin import admin_bp
    from app.controllers.cliente import cliente_bp
    from app.controllers.clientes_admin import clientes_admin_bp
    from app.controllers.conductor import conductor_bp
    from app.controllers.inventario import inventario_bp
    from app.controllers.pedidos import pedidos_bp
    from app.controllers.principal import principal_bp
    from app.controllers.rutas import rutas_bp
    from app.controllers.usuarios import usuarios_bp

    app.register_blueprint(principal_bp)
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(pedidos_bp, url_prefix="/pedidos")
    app.register_blueprint(rutas_bp, url_prefix="/rutas")
    app.register_blueprint(inventario_bp, url_prefix="/inventario")
    app.register_blueprint(conductor_bp, url_prefix="/conductor")
    app.register_blueprint(cliente_bp, url_prefix="/portal")
    app.register_blueprint(usuarios_bp, url_prefix="/admin/usuarios")
    app.register_blueprint(clientes_admin_bp, url_prefix="/admin/clientes")


def _registrar_errores(app):
    from flask_wtf.csrf import CSRFError

    @app.errorhandler(CSRFError)
    def token_invalido(error):
        """Un token vencido o ausente produce por defecto una pagina cruda en
        ingles. En terreno la causa habitual es que el conductor dejo la pantalla
        abierta y la sesion caduco, asi que se le explica en su idioma."""
        return render_template("errores/csrf.html"), 400

    @app.errorhandler(403)
    def prohibido(error):
        return render_template("errores/403.html"), 403

    @app.errorhandler(404)
    def no_encontrado(error):
        return render_template("errores/404.html"), 404

    @app.errorhandler(500)
    def error_interno(error):
        db.session.rollback()
        return render_template("errores/500.html"), 500


def _registrar_contexto(app):
    from app.models import EstadoPedido, EstadoRuta

    @app.context_processor
    def inyectar_globales():
        return {
            "EstadoPedido": EstadoPedido,
            "EstadoRuta": EstadoRuta,
            "nombre_sistema": "SGDS - Sistema de Gestion de Despachos",
            "cd_nombre": app.config["CD_NOMBRE"],
        }
