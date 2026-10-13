"""Auditoria de acciones: quien hizo que, desde donde y con que resultado.

Registra las acciones confirmadas por voz y, en pantalla, las de usuarios,
configuracion, clientes, sedes, productos, ajustes de inventario, vehiculos,
rutas, pedidos y solicitudes (ver app.services.auditoria). Es un historico:
solo se agregan filas; el ORM rechaza modificarlas o borrarlas.

`detalle` guarda JSON con los datos de la accion. Nunca lleva contrasenas ni
tokens: `app.services.auditoria` los quita antes de guardar.
"""

import json

from sqlalchemy import event

from app.extensions import db
from app.tiempo import ahora

RESULTADO_EXITO = "exito"
RESULTADO_ERROR = "error"


class Auditoria(db.Model):
    __tablename__ = "auditoria"

    id = db.Column(db.Integer, primary_key=True)
    # Nombre y rol se copian: el registro dice quien era al momento de la accion.
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), index=True)
    usuario_nombre = db.Column(db.String(120), nullable=False)
    rol = db.Column(db.String(20))
    origen = db.Column(db.String(20), nullable=False, index=True)
    accion = db.Column(db.String(80), nullable=False, index=True)
    entidad = db.Column(db.String(40), nullable=False)
    entidad_id = db.Column(db.String(60))
    resumen = db.Column(db.String(500), nullable=False)
    detalle = db.Column(db.Text)
    resultado = db.Column(db.String(10), nullable=False, default=RESULTADO_EXITO)
    call_id = db.Column(db.String(64))
    registrado_en = db.Column(db.DateTime, nullable=False, default=ahora, index=True)

    usuario = db.relationship("Usuario")

    @property
    def detalle_dict(self):
        if not self.detalle:
            return {}
        try:
            return json.loads(self.detalle)
        except ValueError:
            return {"detalle": self.detalle}

    @property
    def detalle_legible(self):
        """El detalle en texto plano, una linea por dato, para correos y pantalla."""
        return "\n".join(_lineas(self.detalle_dict))

    def __repr__(self):
        return f"<Auditoria {self.id} {self.accion} {self.resultado}>"


def _valor(valor):
    if valor is None or valor == "":
        return "(vacío)"
    if isinstance(valor, bool):
        return "sí" if valor else "no"
    return str(valor)


def _lineas(datos, prefijo=""):
    for clave, valor in datos.items():
        etiqueta = f"{prefijo}{str(clave).replace('_', ' ')}"
        if isinstance(valor, dict):
            yield from _lineas(valor, f"{etiqueta} / ")
        elif isinstance(valor, (list, tuple)) and len(valor) == 2 and \
                not any(isinstance(v, (dict, list, tuple)) for v in valor):
            # (antes, despues) de un cambio
            yield f"{etiqueta}: {_valor(valor[0])} → {_valor(valor[1])}"
        elif isinstance(valor, (list, tuple)):
            yield f"{etiqueta}: " + ", ".join(_valor(v) for v in valor)
        else:
            yield f"{etiqueta}: {_valor(valor)}"


@event.listens_for(Auditoria, "before_update")
def _no_modificar(mapper, conexion, registro):
    raise ValueError("La auditoria no se modifica: solo se agregan registros.")


@event.listens_for(Auditoria, "before_delete")
def _no_borrar(mapper, conexion, registro):
    raise ValueError("La auditoria no se borra: solo se agregan registros.")
