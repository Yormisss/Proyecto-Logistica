"""Configuracion del negocio: catalogo de motivos de fallo y centro de distribucion.

Punto unico de las reglas que comparten la pantalla "Configuracion" del admin,
la pantalla del conductor, la analitica y el asistente de voz. Ninguna funcion
hace commit: el llamador confirma la transaccion.
"""

from difflib import SequenceMatcher

from flask import current_app

from app.extensions import db
from app.models import NOMBRE_OTRO, ConfiguracionNegocio, MotivoFallo, normalizar_texto
from app.tiempo import ahora

LARGO_MAXIMO_MOTIVO = 160
LARGO_MAXIMO_CENTRO = 120

# Los que estaban fijos en el codigo antes del catalogo; "Otro" se agrega al final.
MOTIVOS_INICIALES = (
    "Cliente ausente",
    "Direccion incorrecta",
    "Establecimiento cerrado",
    "Cliente rechaza el pedido",
    "Zona de dificil acceso",
    "Fuera de la ventana horaria",
    "Novedad con la mercancia",
)

# Parecido minimo para reconocer por voz un motivo del catalogo mal transcrito.
PARECIDO_MOTIVO = 0.85


class ConfiguracionInvalida(Exception):
    """El cambio no cumple las reglas. `errores` es una lista de (campo, mensaje)."""

    def __init__(self, errores):
        super().__init__(" ".join(mensaje for _, mensaje in errores))
        self.errores = errores


# --------------------------------------------------------------------------
# Catalogo de motivos de fallo
# --------------------------------------------------------------------------

def asegurar_catalogo():
    """Crea los motivos iniciales y "Otro" si faltan. Idempotente; no hace commit.

    Devuelve los nombres que agrego.
    """
    # Tambien por el nombre con que nacio: un motivo inicial renombrado ya existe.
    existentes = set()
    for m in db.session.query(MotivoFallo):
        existentes.update((m.nombre_normalizado, m.clave_original))
    agregados = []
    for orden, nombre in enumerate(MOTIVOS_INICIALES, start=1):
        clave = normalizar_texto(nombre)
        if clave not in existentes:
            db.session.add(MotivoFallo(nombre=nombre, nombre_normalizado=clave, clave_original=clave,
                                       orden=orden, activo=True))
            agregados.append(nombre)
    if not db.session.query(MotivoFallo).filter_by(es_otro=True).first():
        clave = normalizar_texto(NOMBRE_OTRO)
        db.session.add(MotivoFallo(nombre=NOMBRE_OTRO, nombre_normalizado=clave, clave_original=clave,
                                   orden=1000, activo=True, es_otro=True))
        agregados.append(NOMBRE_OTRO)
    db.session.flush()
    return agregados


def motivo_otro():
    return db.session.query(MotivoFallo).filter_by(es_otro=True).one()


def catalogo(incluir_inactivos=False):
    """Motivos en el orden en que se ofrecen; "Otro" siempre al final."""
    consulta = db.session.query(MotivoFallo)
    if not incluir_inactivos:
        consulta = consulta.filter(MotivoFallo.activo.is_(True))
    return consulta.order_by(MotivoFallo.es_otro, MotivoFallo.orden, MotivoFallo.nombre).all()


def _validar_nombre(nombre, propio=None):
    nombre = (nombre or "").strip()
    errores = []
    if not nombre:
        errores.append(("nombre", "Indique el nombre del motivo."))
    elif len(nombre) > LARGO_MAXIMO_MOTIVO:
        errores.append(("nombre", f"El motivo no puede pasar de {LARGO_MAXIMO_MOTIVO} caracteres."))
    else:
        clave = normalizar_texto(nombre)
        if not clave:
            errores.append(("nombre", "Indique el nombre del motivo."))
        elif clave == normalizar_texto(NOMBRE_OTRO):
            errores.append(("nombre", f"'{NOMBRE_OTRO}' ya existe y es el motivo comodín."))
        else:
            choque = db.session.query(MotivoFallo).filter(MotivoFallo.nombre_normalizado == clave)
            if propio is not None:
                choque = choque.filter(MotivoFallo.id != propio.id)
            gemelo = choque.first()
            if gemelo is not None:
                estado = "" if gemelo.activo else " (está desactivado; actívelo)"
                errores.append(("nombre", f"Ya existe el motivo '{gemelo.nombre}'{estado}."))
    return nombre, errores


def _no_es_otro(motivo, accion):
    if motivo.es_otro:
        raise ConfiguracionInvalida([(None, f"El motivo '{NOMBRE_OTRO}' no se puede {accion}.")])


def validar_agregar_motivo(nombre):
    nombre, errores = _validar_nombre(nombre)
    if errores:
        raise ConfiguracionInvalida(errores)
    return nombre


def agregar_motivo(nombre):
    nombre = validar_agregar_motivo(nombre)
    clave = normalizar_texto(nombre)
    orden = (db.session.query(db.func.max(MotivoFallo.orden))
             .filter(MotivoFallo.es_otro.is_(False)).scalar() or 0) + 1
    motivo = MotivoFallo(nombre=nombre, nombre_normalizado=clave, clave_original=clave,
                         orden=orden, activo=True)
    db.session.add(motivo)
    db.session.flush()
    return motivo


def validar_renombrar_motivo(motivo, nombre):
    _no_es_otro(motivo, "renombrar")
    nombre, errores = _validar_nombre(nombre, propio=motivo)
    if errores:
        raise ConfiguracionInvalida(errores)
    if nombre == motivo.nombre:
        raise ConfiguracionInvalida([("nombre", "El motivo ya tiene ese nombre.")])
    return nombre


def renombrar_motivo(motivo, nombre):
    """Cambia el nombre. Las pruebas de entrega ya registradas conservan su texto."""
    anterior = motivo.nombre
    motivo.nombre = validar_renombrar_motivo(motivo, nombre)
    motivo.nombre_normalizado = normalizar_texto(motivo.nombre)
    return anterior


def validar_cambio_estado_motivo(motivo, activo):
    _no_es_otro(motivo, "activar ni desactivar")
    if motivo.activo == activo:
        raise ConfiguracionInvalida(
            [(None, f"El motivo '{motivo.nombre}' ya está {'activo' if activo else 'desactivado'}.")]
        )


def cambiar_estado_motivo(motivo, activo):
    validar_cambio_estado_motivo(motivo, activo)
    motivo.activo = activo


def motivo_por_nombre(nombre, solo_activos=True):
    """Motivo cuyo nombre normalizado coincide exactamente, o None."""
    consulta = db.session.query(MotivoFallo).filter(
        MotivoFallo.nombre_normalizado == normalizar_texto(nombre)
    )
    if solo_activos:
        consulta = consulta.filter(MotivoFallo.activo.is_(True))
    return consulta.first()


def resolver_motivo_pantalla(nombre_elegido, texto_otro=None):
    """(motivo, texto a guardar) para el formulario del conductor.

    El conductor elige un motivo activo del catalogo. Si elige "Otro", debe
    escribir el motivo (hasta 160 caracteres) y ese texto es el que se guarda.
    """
    nombre_elegido = (nombre_elegido or "").strip()
    if not nombre_elegido:
        raise ConfiguracionInvalida([("motivo_fallo", "Indique el motivo por el cual no se pudo entregar.")])
    motivo = motivo_por_nombre(nombre_elegido)
    if motivo is None:
        raise ConfiguracionInvalida([("motivo_fallo", "Seleccione un motivo de la lista.")])
    if motivo.es_otro:
        texto = (texto_otro or "").strip()
        if not texto:
            raise ConfiguracionInvalida([("motivo_otro", "Escriba el motivo cuando elige 'Otro'.")])
        if len(texto) > LARGO_MAXIMO_MOTIVO:
            raise ConfiguracionInvalida(
                [("motivo_otro", f"El motivo no puede pasar de {LARGO_MAXIMO_MOTIVO} caracteres.")]
            )
        return motivo, texto
    return motivo, motivo.nombre


def interpretar_motivo_voz(texto):
    """(motivo, texto a guardar) para un motivo dicho por voz.

    Si lo dicho coincide con un motivo activo (identico, o muy parecido y sin
    ambiguedad), se usa ese; si no, es "Otro" con el texto dicho. El asistente
    lee cual se va a registrar antes de pedir confirmacion.
    """
    texto = (texto or "").strip()
    if not texto:
        raise ConfiguracionInvalida([("motivo", "Necesito el motivo por el que no se pudo entregar.")])
    if len(texto) > LARGO_MAXIMO_MOTIVO:
        raise ConfiguracionInvalida(
            [("motivo", f"El motivo no puede pasar de {LARGO_MAXIMO_MOTIVO} caracteres.")]
        )
    clave = normalizar_texto(texto)
    activos = [m for m in catalogo() if not m.es_otro]
    for motivo in activos:
        if motivo.nombre_normalizado == clave:
            return motivo, motivo.nombre
    parecidos = [m for m in activos
                 if SequenceMatcher(None, clave, m.nombre_normalizado).ratio() >= PARECIDO_MOTIVO]
    if len(parecidos) == 1:
        return parecidos[0], parecidos[0].nombre
    otro = motivo_otro()
    if normalizar_texto(texto) == otro.nombre_normalizado:
        raise ConfiguracionInvalida([("motivo", "Dime cuál fue el motivo, no solo «otro».")])
    return otro, texto


def reconocer_motivo_historico(texto):
    """Motivo del catalogo para el texto de una prueba anterior al catalogo, o None."""
    clave = normalizar_texto(texto)
    if not clave:
        return None
    return (
        db.session.query(MotivoFallo)
        .filter(db.or_(MotivoFallo.clave_original == clave, MotivoFallo.nombre_normalizado == clave))
        .order_by(MotivoFallo.es_otro)
        .first()
    )


# --------------------------------------------------------------------------
# Centro de distribucion
# --------------------------------------------------------------------------

def _fila_configuracion():
    return db.session.query(ConfiguracionNegocio).order_by(ConfiguracionNegocio.id).first()


def centro_distribucion():
    """{"nombre", "latitud", "longitud"}: de la tabla si existe, si no del .env."""
    fila = _fila_configuracion()
    if fila is not None:
        return {"nombre": fila.cd_nombre, "latitud": fila.cd_latitud, "longitud": fila.cd_longitud}
    config = current_app.config
    return {"nombre": config["CD_NOMBRE"], "latitud": config["CD_LAT"], "longitud": config["CD_LNG"]}


def asegurar_configuracion():
    """Crea la fila con los valores del .env si no existe. Idempotente; no hace commit."""
    if _fila_configuracion() is not None:
        return False
    actual = centro_distribucion()
    db.session.add(ConfiguracionNegocio(cd_nombre=actual["nombre"], cd_latitud=actual["latitud"],
                                        cd_longitud=actual["longitud"], actualizado_en=ahora()))
    db.session.flush()
    return True


def _numero(valor):
    if isinstance(valor, bool):
        return None
    try:
        return float(str(valor).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None


def validar_centro(nombre, latitud, longitud):
    """(nombre, latitud, longitud) validados; lanza ConfiguracionInvalida si no."""
    errores = []
    nombre = (nombre or "").strip()
    if not nombre:
        errores.append(("cd_nombre", "Indique el nombre del centro de distribución."))
    elif len(nombre) > LARGO_MAXIMO_CENTRO:
        errores.append(("cd_nombre", f"El nombre no puede pasar de {LARGO_MAXIMO_CENTRO} caracteres."))
    lat, lng = _numero(latitud), _numero(longitud)
    if lat is None or not -90 <= lat <= 90:
        errores.append(("cd_latitud", "La latitud debe ser un número entre -90 y 90."))
    if lng is None or not -180 <= lng <= 180:
        errores.append(("cd_longitud", "La longitud debe ser un número entre -180 y 180."))
    if errores:
        raise ConfiguracionInvalida(errores)
    return nombre, lat, lng


def editar_centro(nombre, latitud, longitud, usuario_id):
    """Actualiza el centro. Solo afecta a las rutas nuevas o recalculadas.

    Devuelve (anterior, nuevo) como diccionarios, para la auditoria.
    """
    nombre, lat, lng = validar_centro(nombre, latitud, longitud)
    anterior = centro_distribucion()
    asegurar_configuracion()
    fila = _fila_configuracion()
    fila.cd_nombre, fila.cd_latitud, fila.cd_longitud = nombre, lat, lng
    fila.actualizado_en = ahora()
    fila.actualizado_por_id = usuario_id
    return anterior, centro_distribucion()
