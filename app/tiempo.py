"""Zona horaria de la operacion.

El centro de distribucion opera en Bogota, pero el servidor puede correr en
cualquier huso horario (la mayoria de plataformas cloud gratuitas usa UTC).
El "dia de despacho", las ventanas horarias comerciales y las marcas de tiempo
de la bitacora deben reflejar la hora de Bogota, no la del servidor: un
`date.today()` o `datetime.utcnow()` corridos a las 7 de la noche en Bogota
(medianoche en UTC) fechan la operacion al dia siguiente, y una prueba de
entrega comparada contra una ventana horaria en hora del servidor puede
reportarse fuera de horario aunque el conductor haya llegado a tiempo.

Colombia no observa horario de verano, asi que el desplazamiento de Bogota es
siempre UTC-5; aun asi se usa `ZoneInfo` en vez de fijar el offset a mano para
que el calculo quede documentado y resistente a cualquier cambio futuro de la
base de datos de husos horarios (tzdata).
"""

from datetime import datetime
from zoneinfo import ZoneInfo

ZONA_BOGOTA = ZoneInfo("America/Bogota")


def ahora():
    """Fecha y hora actuales en Bogota, como datetime naive (hora de pared).

    Se devuelve sin tzinfo porque las columnas DateTime del modelo no la
    conservan (SQLite y MySQL guardan la hora tal cual, sin desplazamiento):
    mantenerla naive evita mezclar datetimes aware y naive al comparar contra
    los valores ya persistidos en la base.
    """
    return datetime.now(ZONA_BOGOTA).replace(tzinfo=None)


def hoy():
    """Fecha del dia de operacion en Bogota."""
    return ahora().date()
