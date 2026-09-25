"""RNF2 - Rendimiento y Tiempos de Respuesta.

Registra la duracion de cada peticion para poder evidenciar con datos reales que
las transacciones criticas (actualizacion del estado de entrega en terreno y su
reflejo en el tablero del administrador) se procesan dentro del limite de 3 a 5
segundos establecido por el requisito.
"""

from app.extensions import db
from app.tiempo import ahora

# Umbrales del RNF2, en milisegundos.
UMBRAL_OBJETIVO_MS = 3000   # Meta
UMBRAL_MAXIMO_MS = 5000     # Limite tolerado

# Transacciones criticas nombradas por el requisito.
ENDPOINTS_CRITICOS = {
    "conductor.entregar": "Confirmar entrega (terreno)",
    "conductor.fallar": "Registrar entrega fallida",
    "conductor.marcar_en_ruta": "Marcar en ruta",
    "conductor.iniciar": "Iniciar ruta",
    "conductor.mi_ruta": "Consultar ruta del dia",
    "conductor.parada": "Abrir una parada",
    "admin.dashboard": "Tablero del administrador",
}


class MedicionRendimiento(db.Model):
    __tablename__ = "mediciones_rendimiento"

    id = db.Column(db.Integer, primary_key=True)
    endpoint = db.Column(db.String(80), nullable=False, index=True)
    metodo = db.Column(db.String(10), nullable=False)
    estado_http = db.Column(db.Integer, nullable=False)
    duracion_ms = db.Column(db.Float, nullable=False)
    registrado_en = db.Column(db.DateTime, default=ahora, index=True)

    @property
    def es_critico(self):
        return self.endpoint in ENDPOINTS_CRITICOS

    @property
    def cumple(self):
        return self.duracion_ms <= UMBRAL_MAXIMO_MS

    def __repr__(self):
        return f"<Medicion {self.endpoint} {self.duracion_ms:.0f}ms>"
