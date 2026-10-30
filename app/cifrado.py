"""RNF5 - Cifrado de los datos personales de las cuentas en la base.

`documento` y `telefono` de `usuarios` se guardan cifrados con Fernet (AES-128
en modo CBC con HMAC-SHA256, de la libreria `cryptography`). Cada valor lleva
un IV aleatorio: el mismo dato produce textos cifrados distintos, asi que la
base no revela ni siquiera que dos cuentas comparten un dato. El ORM cifra al
guardar y descifra al leer (`TextoCifrado`): el resto de la aplicacion
trabaja con el valor en claro.

Como el texto cifrado no se puede comparar en SQL, `TextoCifrado` rechaza
toda comparacion salvo IS NULL, para que una consulta olvidada falle en vez
de no encontrar nada en silencio. El documento lleva ademas un indice ciego
(`indice_ciego`): un HMAC-SHA256 del valor con otra clave, que permite exigir
que no se repita y buscarlo por igualdad sin guardarlo en claro.

Las dos claves se derivan con HKDF de CLAVE_CIFRADO o, sin ella, de
SECRET_KEY. Si la clave cambia, los datos cifrados con la anterior ya no se
pueden leer: en produccion defina CLAVE_CIFRADO y no la cambie.

La contrasena no se cifra: se guarda su hash (PBKDF2, ver models/usuario.py),
que no se puede revertir ni con la clave.
"""

import base64
import hashlib
import hmac
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from flask import current_app
from sqlalchemy import String
from sqlalchemy.sql import operators
from sqlalchemy.types import TypeDecorator

INFO_HKDF = b"rutalytics-datos-personales"

# Datos personales de una cuenta: cifrados en `usuarios` y enmascarados en la
# auditoria (ver app/services/auditoria.py).
CAMPOS_PERSONALES = ("documento", "telefono")


@lru_cache(maxsize=4)
def _claves(secreto):
    """(Fernet, clave del indice ciego) derivadas de `secreto`."""
    material = HKDF(algorithm=hashes.SHA256(), length=64, salt=None,
                    info=INFO_HKDF).derive(secreto.encode())
    return Fernet(base64.urlsafe_b64encode(material[:32])), material[32:]


def _claves_actuales():
    config = current_app.config
    return _claves(config.get("CLAVE_CIFRADO") or config["SECRET_KEY"])


def cifrar(valor):
    return _claves_actuales()[0].encrypt(str(valor).encode()).decode()


def descifrar(valor):
    try:
        return _claves_actuales()[0].decrypt(valor.encode()).decode()
    except InvalidToken:
        raise ValueError(
            "No se pudo descifrar un dato personal: la clave de cifrado (CLAVE_CIFRADO o, sin "
            "ella, SECRET_KEY) no es la misma con la que se guardo."
        ) from None


def esta_cifrado(valor):
    """True si `valor` es un texto cifrado con la clave actual."""
    try:
        descifrar(valor)
        return True
    except ValueError:
        return False


def indice_ciego(valor):
    """HMAC-SHA256 del valor (sin espacios en los extremos), o None si esta vacio."""
    if valor is None or not str(valor).strip():
        return None
    return hmac.new(_claves_actuales()[1], str(valor).strip().encode(), hashlib.sha256).hexdigest()


def enmascarar(valor):
    """"******4567": solo los 4 ultimos caracteres, sin revelar el largo."""
    if valor is None or valor == "":
        return valor
    texto = str(valor)
    return "******" + texto[-4:] if len(texto) > 4 else "****"


class TextoCifrado(TypeDecorator):
    """Columna de texto que se guarda cifrada. Solo admite IS NULL en SQL."""

    impl = String
    cache_ok = True

    class comparator_factory(TypeDecorator.Comparator):
        def operate(self, op, *other, **kwargs):
            if op in (operators.is_, operators.is_not):
                return super().operate(op, *other, **kwargs)
            raise TypeError(
                "Una columna cifrada no se compara ni se ordena en SQL; para el documento "
                "use `documento_indice` con `indice_ciego`."
            )

        def reverse_operate(self, op, other, **kwargs):
            raise TypeError("Una columna cifrada no se compara en SQL.")

    def process_bind_param(self, valor, dialect):
        return None if valor is None else cifrar(valor)

    def process_result_value(self, valor, dialect):
        return None if valor is None else descifrar(valor)
