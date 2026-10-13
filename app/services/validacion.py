"""Validacion compartida por la pantalla y el asistente de voz.

Los servicios validan con un `Validador`, que distingue lo que falta de lo que
es invalido. La pantalla muestra cada error junto a su campo; el asistente de
voz arma con lo mismo frases para leer en voz alta ("Me falta el nombre y la
direccion. El correo no es valido."), de modo que las reglas son unas solas.
"""

from datetime import time

from email_validator import EmailNotValidError, validate_email


class DatosInvalidos(Exception):
    """Los datos no cumplen las reglas.

    `faltantes` es una lista de (campo, etiqueta) de lo obligatorio que no vino;
    `invalidos`, una lista de (campo, mensaje). `errores` los une como
    (campo, mensaje) para la pantalla.
    """

    def __init__(self, errores=None, faltantes=None):
        self.invalidos = list(errores or [])
        self.faltantes = list(faltantes or [])
        super().__init__(" ".join(mensaje for _, mensaje in self.errores))

    @property
    def errores(self):
        return [(campo, f"Indique {etiqueta}.") for campo, etiqueta in self.faltantes] + self.invalidos


class Validador:
    """Acumula errores mientras se leen y normalizan los datos."""

    def __init__(self):
        self.faltantes = []
        self.invalidos = []

    def falta(self, campo, etiqueta):
        self.faltantes.append((campo, etiqueta))

    def invalido(self, campo, mensaje):
        self.invalidos.append((campo, mensaje))

    @property
    def hay_errores(self):
        return bool(self.faltantes or self.invalidos)

    def lanzar_si_hay(self):
        if self.hay_errores:
            raise DatosInvalidos(self.invalidos, self.faltantes)

    # --- Lectores: devuelven el valor normalizado, o None si falta o no sirve.

    def texto(self, datos, campo, etiqueta, maximo, requerido=False, defecto=None):
        valor = datos.get(campo)
        valor = "" if valor is None else str(valor).strip()
        if not valor:
            if requerido:
                self.falta(campo, etiqueta)
            return defecto
        if len(valor) > maximo:
            self.invalido(campo, f"{etiqueta[0].upper()}{etiqueta[1:]} no puede pasar de "
                                 f"{maximo} caracteres.")
            return None
        return valor

    def correo(self, datos, campo, etiqueta, requerido=False, maximo=120):
        valor = self.texto(datos, campo, etiqueta, maximo, requerido)
        if not valor:
            return valor
        try:
            validate_email(valor, check_deliverability=False)
        except EmailNotValidError:
            self.invalido(campo, f"{etiqueta[0].upper()}{etiqueta[1:]} no es válido.")
            return None
        return valor.lower()

    def entero(self, datos, campo, etiqueta, minimo=None, maximo=None, requerido=False, defecto=None):
        valor = datos.get(campo)
        if valor is None or (isinstance(valor, str) and not valor.strip()):
            if requerido:
                self.falta(campo, etiqueta)
            return defecto
        numero = _entero(valor)
        if numero is None:
            self.invalido(campo, f"{etiqueta[0].upper()}{etiqueta[1:]} debe ser un número entero.")
            return None
        if (minimo is not None and numero < minimo) or (maximo is not None and numero > maximo):
            self.invalido(campo, f"{etiqueta[0].upper()}{etiqueta[1:]} debe estar "
                                 + _rango(minimo, maximo) + ".")
            return None
        return numero

    def decimal(self, datos, campo, etiqueta, minimo=None, maximo=None, requerido=False):
        valor = datos.get(campo)
        if valor is None or (isinstance(valor, str) and not valor.strip()):
            if requerido:
                self.falta(campo, etiqueta)
            return None
        numero = _decimal(valor)
        if numero is None or (minimo is not None and numero < minimo) or \
                (maximo is not None and numero > maximo):
            self.invalido(campo, f"{etiqueta[0].upper()}{etiqueta[1:]} debe ser un número "
                                 + _rango(minimo, maximo) + ".")
            return None
        return numero

    def hora(self, datos, campo, etiqueta):
        valor = datos.get(campo)
        if valor is None or valor == "":
            return None
        if isinstance(valor, time):
            return valor
        hora = _hora(valor)
        if hora is None:
            self.invalido(campo, f"{etiqueta[0].upper()}{etiqueta[1:]} debe ser una hora como 08:30.")
        return hora

    def ventana(self, inicio, fin, campo="ventana_fin"):
        """Si vienen las dos horas, el inicio debe ser anterior al fin; si viene una, ambas."""
        if (inicio is None) != (fin is None):
            self.invalido(campo, "La ventana horaria necesita la hora inicial y la final.")
            return False
        if inicio is not None and inicio >= fin:
            self.invalido(campo, "La hora final debe ser posterior a la inicial.")
            return False
        return True

    def coordenadas(self, datos, campo_lat="latitud", campo_lng="longitud"):
        """(lat, lng) opcionales: las dos o ninguna, dentro de su rango."""
        lat = self.decimal(datos, campo_lat, "la latitud", -90, 90)
        lng = self.decimal(datos, campo_lng, "la longitud", -180, 180)
        vino_lat = datos.get(campo_lat) not in (None, "")
        vino_lng = datos.get(campo_lng) not in (None, "")
        if vino_lat != vino_lng:
            self.invalido(campo_lng if vino_lat else campo_lat,
                          "Las coordenadas necesitan la latitud y la longitud.")
            return None, None
        return lat, lng


def _rango(minimo, maximo):
    if minimo is not None and maximo is not None:
        return f"entre {_numero_texto(minimo)} y {_numero_texto(maximo)}"
    if minimo is not None:
        return f"mayor o igual a {_numero_texto(minimo)}"
    return f"menor o igual a {_numero_texto(maximo)}"


def _numero_texto(valor):
    return str(int(valor)) if float(valor).is_integer() else str(valor)


def _entero(valor):
    if isinstance(valor, bool):
        return None
    if isinstance(valor, int):
        return valor
    if isinstance(valor, float):
        return int(valor) if valor.is_integer() else None
    texto = str(valor).strip()
    if texto.lstrip("-").isdigit():
        return int(texto)
    return None


def _decimal(valor):
    if isinstance(valor, bool):
        return None
    try:
        return float(str(valor).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None


def _hora(valor):
    texto = str(valor).strip().lower().replace(".", ":").replace("h", ":")
    partes = [p for p in texto.split(":") if p != ""]
    if not partes or len(partes) > 3 or not all(p.isdigit() for p in partes):
        return None
    horas, minutos = int(partes[0]), int(partes[1]) if len(partes) > 1 else 0
    if not (0 <= horas <= 23 and 0 <= minutos <= 59):
        return None
    return time(horas, minutos)
