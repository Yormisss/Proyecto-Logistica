"""Flota de transporte: reglas compartidas por la pantalla y la voz.

La placa es unica y queda fija una vez creado el vehiculo. Un vehiculo que
esta en una ruta abierta (planificada o en curso) no se puede desactivar. Un
conductor tiene a lo sumo un vehiculo asignado. Ninguna funcion hace commit.
"""

import re

from app.extensions import db
from app.models import EstadoRuta, Rol, Ruta, Usuario, Vehiculo
from app.services.validacion import DatosInvalidos, Validador

CAMPOS_VEHICULO = ("placa", "tipo", "capacidad_kg", "capacidad_unidades", "conductor_id", "activo")


def _booleano(valor, defecto=True):
    if valor is None or valor == "":
        return defecto
    if isinstance(valor, str):
        return valor.strip().lower() in ("1", "true", "si", "sí", "on", "y", "yes")
    return bool(valor)


def clave_placa(placa):
    """"wgt 482" y "WGT-482" -> "WGT482": la placa se compara sin espacios ni guiones."""
    return re.sub(r"[^A-Z0-9]", "", str(placa or "").upper())


def _placa_escrita(placa):
    """Se guarda como se escribio, en mayusculas y sin espacios sobrantes."""
    return re.sub(r"\s+", " ", str(placa or "").strip().upper())


def vehiculo_por_placa(placa, excluyendo=None):
    clave = clave_placa(placa)
    for vehiculo in db.session.query(Vehiculo):
        if clave_placa(vehiculo.placa) == clave and vehiculo is not excluyendo:
            return vehiculo
    return None


def rutas_abiertas(vehiculo):
    return (
        db.session.query(Ruta)
        .filter(Ruta.vehiculo_id == vehiculo.id,
                Ruta.estado.in_((EstadoRuta.PLANIFICADA, EstadoRuta.EN_CURSO)))
        .count()
    )


def validar_vehiculo(datos, vehiculo=None):
    """Datos normalizados de un vehiculo; lanza DatosInvalidos si no cumplen."""
    actuales = {campo: getattr(vehiculo, campo) for campo in CAMPOS_VEHICULO} if vehiculo else {}
    datos = {**actuales, **datos}
    v = Validador()

    placa_dicha = v.texto(datos, "placa", "la placa", 15, requerido=True)
    placa = _placa_escrita(placa_dicha) if placa_dicha else None
    if placa and not clave_placa(placa):
        v.invalido("placa", "La placa debe tener letras o números.")
        placa = None
    if vehiculo is not None and placa and clave_placa(placa) != clave_placa(vehiculo.placa):
        v.invalido("placa", "La placa queda fija una vez creado el vehículo.")
    if vehiculo is not None:
        placa = vehiculo.placa
    if placa:
        gemelo = vehiculo_por_placa(placa, excluyendo=vehiculo)
        if gemelo is not None:
            v.invalido("placa", f"Ya existe un vehículo con la placa {gemelo.placa}.")

    tipo = v.texto(datos, "tipo", "el tipo", 40, defecto="Furgon")
    capacidad_kg = v.decimal(datos, "capacidad_kg", "la capacidad en kilos", minimo=0)
    capacidad_unidades = v.entero(datos, "capacidad_unidades", "la capacidad en unidades", minimo=0, defecto=0)
    activo = _booleano(datos.get("activo"))

    conductor = None
    conductor_id = datos.get("conductor_id")
    if conductor_id not in (None, "", 0, "0"):
        try:
            conductor = db.session.get(Usuario, int(conductor_id))
        except (TypeError, ValueError):
            conductor = None
        if conductor is None or conductor.rol != Rol.CONDUCTOR or not conductor.activo:
            v.invalido("conductor_id", "El conductor no existe o no está activo.")
            conductor = None
        elif conductor.vehiculo is not None and (vehiculo is None or conductor.vehiculo.id != vehiculo.id):
            v.invalido("conductor_id", f"{conductor.nombre} ya tiene asignado el vehículo "
                                       f"{conductor.vehiculo.placa}.")
            conductor = None

    if vehiculo is not None and vehiculo.activo and not activo and rutas_abiertas(vehiculo):
        v.invalido("activo", "No se puede desactivar un vehículo que está en una ruta abierta.")

    v.lanzar_si_hay()
    return {"placa": placa, "tipo": tipo, "capacidad_kg": capacidad_kg or 0.0,
            "capacidad_unidades": capacidad_unidades or 0,
            "conductor_id": conductor.id if conductor else None, "activo": activo}


def crear_vehiculo(datos):
    vehiculo = Vehiculo(**validar_vehiculo(datos))
    db.session.add(vehiculo)
    db.session.flush()
    return vehiculo


def editar_vehiculo(vehiculo, datos):
    """Aplica los cambios y devuelve {campo: (antes, despues)}."""
    antes = {campo: getattr(vehiculo, campo) for campo in CAMPOS_VEHICULO}
    limpios = validar_vehiculo(datos, vehiculo)
    cambios = {campo: (antes[campo], valor) for campo, valor in limpios.items() if antes[campo] != valor}
    for campo, valor in limpios.items():
        setattr(vehiculo, campo, valor)
    return cambios


def cambiar_estado_vehiculo(vehiculo, activo):
    if vehiculo.activo == activo:
        raise DatosInvalidos([(None, f"El vehículo {vehiculo.placa} ya está "
                                     f"{'activo' if activo else 'inactivo'}.")])
    if not activo and rutas_abiertas(vehiculo):
        raise DatosInvalidos([("activo", "No se puede desactivar un vehículo que está en una ruta abierta.")])
    vehiculo.activo = activo
