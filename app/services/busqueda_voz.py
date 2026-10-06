"""Busqueda tolerante para el asistente de voz.

Por voz nadie dicta "PED-20261006-005" con guiones ni escribe bien "arroz":
estas funciones resuelven pedidos, productos, clientes y sedes a partir de lo
que el usuario dijo, con la misma normalizacion que deduplica clientes
(`normalizar_texto`: sin acentos, sin puntuacion, en minuscula).

Todas devuelven `Coincidencias`. Si hay una sola, la funcion del asistente la
usa; si hay varias, `describir` las lista para que el agente pregunte cual; si
no hay ninguna, el agente lo dice. Cada busqueda prueba primero la forma mas
exacta (codigo, SKU, nombre identico) y solo si no encuentra nada pasa a una
mas amplia, para que "Tienda La Esquina" no se confunda con "Tienda La
Esquina 2".
"""

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from sqlalchemy import func, or_

from app.extensions import db
from app.models import Cliente, EstadoPedido, Pedido, Producto, Rol, Usuario, normalizar_texto
from app.tiempo import hoy

# Opciones que se leen en voz alta cuando hay varias coincidencias.
LIMITE_OPCIONES = 5

# Parecido minimo (0 a 1) para aceptar una palabra mal dicha o mal transcrita.
PARECIDO_MINIMO = 0.75

# Largo minimo de una palabra para buscar por ella sola ("detergente" si, "dos" no).
LARGO_PALABRA_SIGNIFICATIVA = 4

# Palabras que acompanan al numero del pedido del dia: "el pedido numero 5 de hoy".
_RELLENO_NUMERO = {"el", "la", "pedido", "orden", "numero", "no", "n", "nro", "de", "del",
                   "hoy", "dia"}


@dataclass
class Coincidencias:
    elementos: list

    @property
    def unico(self):
        return self.elementos[0] if len(self.elementos) == 1 else None

    @property
    def vacio(self):
        return not self.elementos

    @property
    def varias(self):
        return len(self.elementos) > 1


def _alfanumerico(texto):
    return re.sub(r"[^A-Z0-9]", "", (texto or "").upper())


def _sin_separadores(columna):
    return func.upper(func.replace(func.replace(columna, "-", ""), " ", ""))


def _palabras(texto):
    return normalizar_texto(texto).split()


def _contiene_palabras(palabras, normalizado):
    return all(palabra in normalizado for palabra in palabras)


def _parecido(palabras, normalizado):
    """Promedio del mejor parecido de cada palabra buscada contra el nombre."""
    destino = normalizado.split()
    if not palabras or not destino:
        return 0.0
    return sum(
        max(SequenceMatcher(None, palabra, otra).ratio() for otra in destino)
        for palabra in palabras
    ) / len(palabras)


def _por_parecido(candidatos, texto, normalizar):
    """Ultimo recurso, en dos pasos.

    1. Todas las palabras dichas se parecen a alguna del nombre ("arros" ->
       "arroz"), ordenadas de la mas parecida a la menos.
    2. Si ninguna, las que contienen mas palabras significativas de lo dicho
       ("detergente dos kilos" -> "Detergente 2 kg").
    """
    palabras = _palabras(texto)
    puntuados = [(_parecido(palabras, normalizar(c)), c) for c in candidatos]
    puntuados = [(p, c) for p, c in puntuados if p >= PARECIDO_MINIMO]
    if puntuados:
        puntuados.sort(key=lambda par: -par[0])
        return [c for _, c in puntuados]

    significativas = [p for p in palabras if len(p) >= LARGO_PALABRA_SIGNIFICATIVA]
    conteos = [(sum(p in normalizar(c) for p in significativas), c) for c in candidatos]
    mejor = max((n for n, _ in conteos), default=0)
    return [c for n, c in conteos if mejor and n == mejor]


# --------------------------------------------------------------------------
# Pedidos
# --------------------------------------------------------------------------

def _numero_del_dia(texto):
    """5 para "5", "pedido 5" o "el numero 5 de hoy"; None si no es solo un numero."""
    palabras = _palabras(texto)
    numeros = [p for p in palabras if p.isdigit()]
    if len(numeros) != 1 or len(numeros[0]) > 4:
        return None
    if any(p not in _RELLENO_NUMERO for p in palabras if not p.isdigit()):
        return None
    return int(numeros[0])


def buscar_pedidos(texto, cliente_id=None, fecha=None):
    """Pedido por codigo, por numero del dia o por nombre del cliente.

    Con `cliente_id` solo busca entre los pedidos de ese cliente: un cliente
    nunca puede encontrar un pedido ajeno, ni siquiera por su codigo exacto.
    Por nombre de cliente solo considera los pedidos de `fecha` o aun abiertos;
    el historico completo no se puede elegir de viva voz.
    """
    fecha = fecha or hoy()
    if not (texto or "").strip():
        return Coincidencias([])

    base = db.session.query(Pedido)
    if cliente_id is not None:
        base = base.filter(Pedido.cliente_id == cliente_id)

    clave = _alfanumerico(texto)
    if len(clave) >= 6:
        exactos = base.filter(_sin_separadores(Pedido.codigo) == clave).all()
        if exactos:
            return Coincidencias(exactos)

    numero = _numero_del_dia(texto)
    if numero is not None:
        codigo = f"PED-{fecha.strftime('%Y%m%d')}-{numero:03d}"
        return Coincidencias(base.filter(Pedido.codigo == codigo).all())

    palabras = _palabras(texto)
    if not palabras:
        return Coincidencias([])
    consulta = (
        base.join(Cliente, Pedido.cliente_id == Cliente.id)
        .filter(or_(Pedido.fecha_despacho == fecha, Pedido.estado.in_(EstadoPedido.ABIERTOS)))
        .order_by(Pedido.fecha_despacho.desc(), Pedido.codigo)
    )
    for palabra in palabras:
        consulta = consulta.filter(Cliente.nombre_normalizado.like(f"%{palabra}%"))
    return Coincidencias(consulta.all())


# --------------------------------------------------------------------------
# Productos
# --------------------------------------------------------------------------

def buscar_productos(texto, solo_activos=True):
    """Producto por SKU ("sku 1001", "1001") o por nombre aproximado."""
    if not (texto or "").strip():
        return Coincidencias([])

    base = db.session.query(Producto)
    if solo_activos:
        base = base.filter(Producto.activo.is_(True))

    clave = _alfanumerico(texto)
    if clave:
        claves = {clave} | ({f"SKU{clave}"} if clave.isdigit() else set())
        exactos = base.filter(_sin_separadores(Producto.sku).in_(claves)).all()
        if exactos:
            return Coincidencias(exactos)

    productos = base.order_by(Producto.nombre).all()
    buscado = normalizar_texto(texto)
    identicos = [p for p in productos if normalizar_texto(p.nombre) == buscado]
    if identicos:
        return Coincidencias(identicos)

    palabras = _palabras(texto)
    contienen = [p for p in productos if _contiene_palabras(palabras, normalizar_texto(p.nombre))]
    if contienen:
        return Coincidencias(contienen)

    return Coincidencias(_por_parecido(productos, texto, lambda p: normalizar_texto(p.nombre)))


# --------------------------------------------------------------------------
# Clientes y sedes
# --------------------------------------------------------------------------

def buscar_clientes(texto):
    """Cliente activo por nombre: identico, que contenga las palabras o parecido."""
    buscado = normalizar_texto(texto)
    if not buscado:
        return Coincidencias([])

    base = db.session.query(Cliente).filter(Cliente.activo.is_(True))
    identicos = base.filter(Cliente.nombre_normalizado == buscado).all()
    if identicos:
        return Coincidencias(identicos)

    consulta = base.order_by(Cliente.nombre)
    for palabra in buscado.split():
        consulta = consulta.filter(Cliente.nombre_normalizado.like(f"%{palabra}%"))
    contienen = consulta.all()
    if contienen:
        return Coincidencias(contienen)

    return Coincidencias(
        _por_parecido(base.order_by(Cliente.nombre).all(), texto, lambda c: c.nombre_normalizado)
    )


def _texto_sede(sede):
    return f"{normalizar_texto(sede.etiqueta)} {sede.direccion_normalizada}"


def buscar_sedes(cliente, texto=None):
    """Sede activa de `cliente` por etiqueta o direccion.

    Sin texto, la unica sede del cliente si solo tiene una; si tiene varias,
    todas, para que el agente pregunte cual.
    """
    sedes = [s for s in cliente.direcciones if s.activa]
    buscado = normalizar_texto(texto)
    if not buscado:
        return Coincidencias(sedes)

    identicas = [s for s in sedes if normalizar_texto(s.etiqueta) == buscado
                 or s.direccion_normalizada == buscado]
    if identicas:
        return Coincidencias(identicas)

    palabras = buscado.split()
    contienen = [s for s in sedes if _contiene_palabras(palabras, _texto_sede(s))]
    if contienen:
        return Coincidencias(contienen)

    return Coincidencias(_por_parecido(sedes, texto, _texto_sede))


# --------------------------------------------------------------------------
# Conductores
# --------------------------------------------------------------------------

def buscar_conductores(texto):
    """Conductor activo por nombre: identico, que contenga las palabras o parecido."""
    buscado = normalizar_texto(texto)
    if not buscado:
        return Coincidencias([])

    conductores = (
        db.session.query(Usuario)
        .filter(Usuario.rol == Rol.CONDUCTOR, Usuario.activo.is_(True))
        .order_by(Usuario.nombre)
        .all()
    )
    identicos = [c for c in conductores if normalizar_texto(c.nombre) == buscado]
    if identicos:
        return Coincidencias(identicos)

    contienen = [c for c in conductores
                 if _contiene_palabras(buscado.split(), normalizar_texto(c.nombre))]
    if contienen:
        return Coincidencias(contienen)

    return Coincidencias(_por_parecido(conductores, texto, lambda c: normalizar_texto(c.nombre)))


# --------------------------------------------------------------------------
# Respuesta en voz
# --------------------------------------------------------------------------

def enumerar(textos):
    if len(textos) == 1:
        return textos[0]
    return ", ".join(textos[:-1]) + " y " + textos[-1]


def describir(coincidencias, como_texto, que):
    """Frase para que el agente pregunte cual: "Encontré 3 pedidos: A, B y C. ¿Cuál?".

    `como_texto` convierte cada elemento en lo que se lee; `que` es el
    sustantivo en plural ("pedidos", "productos").
    """
    elementos = coincidencias.elementos
    leidos = [como_texto(e) for e in elementos[:LIMITE_OPCIONES]]
    sobrantes = len(elementos) - len(leidos)
    lista = enumerar(leidos) + (f", y {sobrantes} más" if sobrantes else "")
    return f"Encontré {len(elementos)} {que}: {lista}. ¿Cuál?"
