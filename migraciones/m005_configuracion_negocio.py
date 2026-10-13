"""Configuracion del negocio: catalogo de motivos de fallo y centro de distribucion.

1. Crea `motivos_fallo` y `configuracion_negocio` si faltan.
2. Agrega `pruebas_entrega.motivo_id` con su indice y, en MySQL, su clave ajena
   (`ALTER TABLE ADD COLUMN` no crea ni el uno ni la otra). Las pruebas ya
   registradas quedan con `motivo_id` vacio: el historico no se edita, y la
   analitica las reconoce por su texto.
3. Carga los motivos que estaban fijos en el codigo, mas "Otro", y la fila del
   centro de distribucion con CD_NOMBRE, CD_LAT y CD_LNG del .env.

Es idempotente: solo hace lo que falte.

    flask --app run migrar-configuracion
"""

from sqlalchemy import inspect, text

from app.extensions import db
from app.models import ConfiguracionNegocio, MotivoFallo
from app.services.configuracion import asegurar_catalogo, asegurar_configuracion

TABLAS = (MotivoFallo.__table__, ConfiguracionNegocio.__table__)
INDICE = "ix_pruebas_entrega_motivo_id"
CLAVE_AJENA = "fk_pruebas_entrega_motivo_id"


def aplicar(verboso=True):
    """Aplica lo que falte. Devuelve un diccionario con lo que hizo."""
    hecho = {"tablas": [], "columnas": [], "indices": [], "claves": [], "motivos": [],
             "centro": False}
    inspector = inspect(db.engine)
    existentes = set(inspector.get_table_names())
    hecho["tablas"] = [t.name for t in TABLAS if t.name not in existentes]
    db.metadata.create_all(bind=db.engine, tables=list(TABLAS), checkfirst=True)

    inspector = inspect(db.engine)
    columnas = {c["name"] for c in inspector.get_columns("pruebas_entrega")}
    if "motivo_id" not in columnas:
        with db.engine.begin() as conexion:
            conexion.execute(text("ALTER TABLE pruebas_entrega ADD COLUMN motivo_id INTEGER"))
        hecho["columnas"].append("pruebas_entrega.motivo_id")

    inspector = inspect(db.engine)
    if INDICE not in {i["name"] for i in inspector.get_indexes("pruebas_entrega")}:
        with db.engine.begin() as conexion:
            conexion.execute(text(f"CREATE INDEX {INDICE} ON pruebas_entrega (motivo_id)"))
        hecho["indices"].append(INDICE)

    # SQLite no admite agregar una clave ajena a una tabla existente; el motor
    # del piloto es MySQL (mismo criterio que m001).
    if db.engine.dialect.name == "mysql":
        declaradas = {c for fk in inspector.get_foreign_keys("pruebas_entrega")
                      for c in fk["constrained_columns"]}
        if "motivo_id" not in declaradas:
            with db.engine.begin() as conexion:
                conexion.execute(text(
                    f"ALTER TABLE pruebas_entrega ADD CONSTRAINT {CLAVE_AJENA} "
                    "FOREIGN KEY (motivo_id) REFERENCES motivos_fallo (id)"
                ))
            hecho["claves"].append(CLAVE_AJENA)

    hecho["motivos"] = asegurar_catalogo()
    hecho["centro"] = asegurar_configuracion()
    db.session.commit()

    if verboso:
        cambios = [f"tablas creadas: {', '.join(hecho['tablas'])}" if hecho["tablas"] else "",
                   f"columnas: {', '.join(hecho['columnas'])}" if hecho["columnas"] else "",
                   f"indices: {', '.join(hecho['indices'])}" if hecho["indices"] else "",
                   f"claves ajenas: {', '.join(hecho['claves'])}" if hecho["claves"] else "",
                   f"motivos cargados: {len(hecho['motivos'])}" if hecho["motivos"] else "",
                   "centro de distribucion tomado del .env" if hecho["centro"] else ""]
        cambios = [c for c in cambios if c]
        print("Configuracion del negocio: " + ("; ".join(cambios) if cambios else "sin cambios."))
    return hecho
