"""Archivos subidos: manda el contenido, no la extension (RNF5).

Cubre un ejecutable renombrado a .pdf y a .csv (Windows, Linux, macOS, script),
otros formatos que no son texto (PDF, comprimidos, imagenes, UTF-16, binario),
que el archivo rechazado no se guarde ni se procese, que quede en la bitacora,
que un CSV valido siga entrando sin falsos positivos y el limite de 5 MB.
"""

import io
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _preparar  # noqa: E402  Primero: base de pruebas, sin Make, Retell ni red externa.
_preparar.reiniciar_base()

from run import app
from app.extensions import db
from app.models import RESULTADO_ERROR, Auditoria
from app.services.archivos import contenido_real

fallos = []
def check(cond, msg):
    print(("  OK   " if cond else "  FALLA") + f" {msg}")
    if not cond: fallos.append(msg)

app.config["WTF_CSRF_ENABLED"] = False
CARPETA = pathlib.Path(app.instance_path) / "importaciones"

gestor = app.test_client()
gestor.post("/auth/login", data={"correo": "despachador@sgds.com", "contrasena": "Despacho123*"})

# Cabecera minima de un ejecutable de Windows: "MZ", bytes nulos y el aviso de DOS.
EXE_WINDOWS = (b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00" + b"\x00" * 44
               + b"\x80\x00\x00\x00" + b"This program cannot be run in DOS mode.\r\r\n$" + b"\x00" * 200
               + b"PE\x00\x00")
CSV_OK = b"""cliente_nombre,direccion,ciudad,latitud,longitud,fecha_despacho,sku,cantidad,codigo
Panaderia Central,Cra 15 #80-10,Bogota,4.6680,-74.0550,{fecha},SKU-1001,4,LOTE-A
""".replace(b"{fecha}", (__import__("app.tiempo", fromlist=["hoy"]).hoy()
                         + __import__("datetime").timedelta(days=2)).isoformat().encode())


def subir(datos, nombre):
    r = gestor.post("/pedidos/importar", data={"archivo": (io.BytesIO(datos), nombre)},
                    content_type="multipart/form-data")
    return r.status_code, r.data.decode()


def guardados():
    return sorted(CARPETA.glob("*.csv")) if CARPETA.exists() else []


def rechazos():
    with app.app_context():
        return db.session.query(Auditoria).filter_by(accion="Rechazar archivo").order_by(Auditoria.id).all()


print("\n== 1. Un ejecutable renombrado a .pdf ==")
antes = guardados()
estado, html = subir(EXE_WINDOWS, "factura.pdf")
check(estado == 200 and "Archivo rechazado por seguridad" in html
      and "programa ejecutable de Windows" in html,
      "se rechaza por su contenido: es un ejecutable de Windows")
check("«factura.pdf» no es un CSV" in html and "No se guardó ni se procesó" in html,
      "el mensaje nombra el archivo y dice que no se guardo")
check(guardados() == antes, "no queda ningun archivo en el servidor")
registro = rechazos()[-1]
check(registro.resultado == RESULTADO_ERROR and "factura.pdf" in registro.resumen
      and "ejecutable de Windows" in registro.resumen, "queda en la bitacora como error")
check(registro.usuario_nombre == "Carlos Rueda", "con quien lo intento")
admin = app.test_client()
admin.post("/auth/login", data={"correo": "admin@sgds.com", "contrasena": "Admin123*"})
bitacora = admin.get("/admin/bitacora/?accion=Rechazar+archivo").data.decode()
check("factura.pdf" in bitacora and "Error" in bitacora, "el admin lo ve en la Bitacora")

print("\n== 2. El mismo ejecutable con extension .csv ==")
estado, html = subir(EXE_WINDOWS, "pedidos.csv")
check("programa ejecutable de Windows" in html, "la extension .csv no lo hace pasar")
check(guardados() == antes, "tampoco se guarda")

print("\n== 3. Otros programas y formatos que no son texto ==")
CASOS = (
    (b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 40, "servidor.pdf", "ejecutable de Linux", True),
    (b"\xcf\xfa\xed\xfe\x07\x00\x00\x01" + b"\x00" * 40, "app.csv", "ejecutable de macOS", True),
    (b"\xca\xfe\xba\xbe\x00\x00\x00\x02" + b"\x00" * 40, "universal.pdf", "ejecutable de macOS", True),
    (b"#!/bin/sh\nrm -rf /\n", "pedidos.csv", "script ejecutable", True),
    (b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n", "pedidos.csv", "documento PDF", False),
    (b"PK\x03\x04\x14\x00\x06\x00" + b"\x00" * 40, "pedidos.csv", "archivo comprimido", False),
    (b"\x89PNG\r\n\x1a\n" + b"\x00" * 40, "pedidos.csv", "imagen PNG", False),
    (b"\xff\xfec\x00l\x00i\x00", "pedidos.csv", "UTF-16", False),
    (b"cliente_nombre,direccion\nA\x00B,C\n", "pedidos.csv", "archivo binario", False),
)
for datos, nombre, esperado, es_programa in CASOS:
    estado, html = subir(datos, nombre)
    prefijo = "Archivo rechazado por seguridad" if es_programa else "Archivo rechazado:"
    check(esperado in html and prefijo in html, f"{nombre} con {esperado}: rechazado")
check(guardados() == antes, "ninguno se guarda")
check(len(rechazos()) == 2 + len(CASOS), "cada rechazo queda en la bitacora")

print("\n== 4. Sin falsos positivos ==")
check(contenido_real(b"MZ,cliente_nombre\n") is None, "un texto que empieza por MZ no es un ejecutable")
check(contenido_real("Panadería,Bogotá\n".encode("utf-8")) is None, "un CSV UTF-8 con tildes es texto")
check(contenido_real("Panadería,Bogotá\n".encode("latin-1")) is None, "uno en latin-1 tambien")
check(contenido_real(b"\xef\xbb\xbfcliente_nombre\n") is None, "y uno con BOM de UTF-8 (Excel)")
estado, html = subir(CSV_OK, "pedidos.csv")
check(estado == 200 and "1 pedido(s) listos" in html, "un CSV valido sigue entrando a la vista previa")
token = re.search(r'name="token" value="([a-f0-9]{32})"', html).group(1)
(CARPETA / f"{token}.csv").unlink(missing_ok=True)
estado, html = subir(b"cliente_nombre,direccion\n", "pedidos.txt")
check("extension .csv" in html, "un texto con otra extension se rechaza por la extension")

print("\n== 5. Limite de tamano ==")
estado, html = subir(b"a" * (2 * 1024 * 1024 + 10), "grande.csv")
check("supera el limite de 2 MB" in html, "un CSV de mas de 2 MB se rechaza")
estado, html = subir(b"a" * (6 * 1024 * 1024), "enorme.csv")
check(estado == 413 and "Archivo demasiado grande" in html,
      "una peticion de mas de 5 MB se corta antes de leerla (413)")
check(guardados() == antes, "ninguno se guarda")


print("\n" + "=" * 55)
print("RESULTADO: " + ("TODAS LAS PRUEBAS PASARON" if not fallos else f"{len(fallos)} FALLAS"))
for f in fallos: print("   - " + f)
sys.exit(1 if fallos else 0)
