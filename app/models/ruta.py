"""RF3 - Generacion de Rutas Basicas: flota y rutas de distribucion de ultima milla."""

from sqlalchemy.dialects import mysql

from app.extensions import db
from app.tiempo import ahora

# La polilinea de una ruta urbana supera con facilidad los 65 KB que admite el
# tipo TEXT de MySQL: una ruta de 5 paradas ya ocupa ~33 KB. Se declara la
# variante MEDIUMTEXT (16 MB) para que el motor del piloto no trunque el trazado.
# SQLite ignora la variante y almacena el texto sin limite practico.
TEXTO_LARGO = db.Text().with_variant(mysql.MEDIUMTEXT(), "mysql")


class Vehiculo(db.Model):
    """Flota de transporte (actor 2.2.2)."""

    __tablename__ = "vehiculos"

    id = db.Column(db.Integer, primary_key=True)
    placa = db.Column(db.String(15), unique=True, nullable=False, index=True)
    tipo = db.Column(db.String(40), default="Furgon")
    capacidad_kg = db.Column(db.Float, default=0)
    capacidad_unidades = db.Column(db.Integer, default=0)
    activo = db.Column(db.Boolean, nullable=False, default=True)
    conductor_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"))

    conductor = db.relationship("Usuario", back_populates="vehiculo")
    rutas = db.relationship("Ruta", back_populates="vehiculo", lazy="dynamic")

    def __repr__(self):
        return f"<Vehiculo {self.placa}>"


class EstadoRuta:
    PLANIFICADA = "PLANIFICADA"
    EN_CURSO = "EN_CURSO"
    FINALIZADA = "FINALIZADA"
    CANCELADA = "CANCELADA"

    ETIQUETAS = {
        PLANIFICADA: "Planificada",
        EN_CURSO: "En curso",
        FINALIZADA: "Finalizada",
        CANCELADA: "Cancelada",
    }


class Ruta(db.Model):
    """Ruta secuencial sugerida para un conductor en una fecha determinada.

    Sustituye la asignacion empirica y estatica identificada en el diagrama de
    Ishikawa (categoria Metodos) por una secuencia calculada.
    """

    __tablename__ = "rutas"

    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(40), unique=True, nullable=False, index=True)
    fecha = db.Column(db.Date, nullable=False, index=True)
    estado = db.Column(db.String(20), nullable=False, default=EstadoRuta.PLANIFICADA, index=True)

    conductor_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    vehiculo_id = db.Column(db.Integer, db.ForeignKey("vehiculos.id"))

    # Metricas devueltas por el proveedor de geolocalizacion (RF3)
    distancia_km = db.Column(db.Float, default=0)
    duracion_min = db.Column(db.Float, default=0)
    proveedor_ruteo = db.Column(db.String(30))
    geometria = db.Column(TEXTO_LARGO)  # Polilinea para dibujar la ruta en el mapa

    creada_en = db.Column(db.DateTime, default=ahora)
    iniciada_en = db.Column(db.DateTime)
    finalizada_en = db.Column(db.DateTime)

    conductor = db.relationship("Usuario", back_populates="rutas")
    vehiculo = db.relationship("Vehiculo", back_populates="rutas")
    pedidos = db.relationship(
        "Pedido", back_populates="ruta", order_by="Pedido.orden_en_ruta"
    )

    @property
    def estado_etiqueta(self):
        return EstadoRuta.ETIQUETAS.get(self.estado, self.estado)

    @property
    def total_paradas(self):
        return len(self.pedidos)

    @property
    def paradas_cerradas(self):
        from app.models.pedido import EstadoPedido

        return sum(1 for p in self.pedidos if p.estado in EstadoPedido.FINALES)

    @property
    def avance_porcentaje(self):
        if not self.total_paradas:
            return 0
        return round(self.paradas_cerradas * 100 / self.total_paradas, 1)

    def __repr__(self):
        return f"<Ruta {self.codigo} {self.fecha} {self.estado}>"
