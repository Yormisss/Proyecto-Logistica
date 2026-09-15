"""Instancias de extensiones compartidas por toda la aplicacion.

Se declaran aparte del application factory para evitar importaciones circulares
entre modelos y controladores.
"""

from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect

db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()

login_manager.login_view = "auth.login"
login_manager.login_message = "Debe iniciar sesion para acceder a esta seccion."
login_manager.login_message_category = "advertencia"
