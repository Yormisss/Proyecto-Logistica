"""Cifrado de los datos personales de las cuentas (RNF5).

Cubre que en la base la contrasena este como hash y el documento y el telefono
cifrados (con IV aleatorio), que la aplicacion los lea en claro, el indice
ciego del documento (unicidad y busqueda exacta), que una columna cifrada no
se pueda comparar en SQL, la auditoria enmascarada en pantalla y por voz, una
clave distinta y la migracion m011 sobre una base con los datos en claro.
"""

import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _preparar  # noqa: E402  Primero: base de pruebas, sin Make, Retell ni red externa.
_preparar.reiniciar_base()
from retell.lib.webhook_auth import symmetric
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from run import app
from app.cifrado import cifrar, descifrar, enmascarar, indice_ciego
from app.extensions import db
from app.models import Auditoria, Rol, SesionAsistente, Usuario, VIGENCIA_SESION
from app.services.auditoria import registrar_auditoria
from app.tiempo import ahora

fallos = []
def check(cond, msg):
    print(("  OK   " if cond else "  FALLA") + f" {msg}")
    if not cond: fallos.append(msg)

app.config["WTF_CSRF_ENABLED"] = False


def sesion(correo, clave):
    c = app.test_client()
    c.post("/auth/login", data={"correo": correo, "contrasena": clave})
    return c


def crudo(correo):
    """La fila tal como esta en la base, sin pasar por el ORM."""
    with app.app_context(), db.engine.connect() as conexion:
        return conexion.execute(text(
            "SELECT documento, telefono, documento_indice, contrasena_hash FROM usuarios "
            "WHERE correo = :correo"), {"correo": correo}).one()


def usuario(correo):
    with app.app_context():
        u = db.session.query(Usuario).filter_by(correo=correo).one()
        db.session.expunge(u)
        return u


print("\n== 1. En la base: contrasena con hash, documento y telefono cifrados ==")
fila = crudo("conductor1@sgds.com")
check(fila.contrasena_hash.startswith("pbkdf2:sha256:600000$") and "Conductor123*" not in fila.contrasena_hash,
      "la contrasena esta como hash PBKDF2-SHA256")
check(fila.documento.startswith("gAAAAA") and "1023456789" not in fila.documento,
      "el documento esta cifrado (Fernet)")
check(fila.telefono.startswith("gAAAAA") and "3001234567" not in fila.telefono, "el telefono tambien")
check(re.fullmatch(r"[0-9a-f]{64}", fila.documento_indice) is not None,
      "el indice ciego es un HMAC-SHA256, sin el documento")
u = usuario("conductor1@sgds.com")
check(u.documento == "1023456789" and u.telefono == "3001234567", "la aplicacion los lee en claro")
with app.app_context():
    sin_cifrar = db.session.execute(text(
        "SELECT COUNT(*) FROM usuarios WHERE (documento IS NOT NULL AND documento NOT LIKE 'gAAAAA%') "
        "OR (telefono IS NOT NULL AND telefono NOT LIKE 'gAAAAA%')")).scalar()
    check(sin_cifrar == 0, "ninguna cuenta sembrada tiene datos personales en claro")
    check(cifrar("52345678") != cifrar("52345678"), "el mismo dato cifrado dos veces da textos distintos")
    check(descifrar(cifrar("52345678")) == "52345678", "y ambos se descifran")
    check(indice_ciego(" 52345678 ") == indice_ciego("52345678") != indice_ciego("52345679"),
          "el indice ciego ignora los espacios de los extremos y distingue los valores")
    check(enmascarar("3001234567") == "******4567" and enmascarar("123") == "****",
          "el enmascarado deja los 4 ultimos caracteres")

print("\n== 2. Una columna cifrada no se compara en SQL ==")
with app.app_context():
    for descripcion, consulta in (("igualdad", lambda: Usuario.documento == "1"),
                                  ("ilike", lambda: Usuario.telefono.ilike("%1%")),
                                  ("orden", lambda: Usuario.documento.desc())):
        try:
            consulta()
            check(False, f"{descripcion}: deberia rechazarse")
        except TypeError:
            check(True, f"{descripcion}: se rechaza con un error, no devuelve vacio en silencio")
    check(db.session.query(Usuario).filter(Usuario.documento.isnot(None)).count() == 5,
          "IS NULL si se puede consultar")

print("\n== 3. Alta, unicidad y busqueda por el indice ciego ==")
admin = sesion("admin@sgds.com", "Admin123*")
r = admin.post("/admin/usuarios/nuevo", data={
    "nombre": "Sofia Leon", "correo": "sofia.leon@sgds.com", "documento": "1012345678",
    "telefono": "3015556677", "rol": Rol.CONDUCTOR, "cliente_id": "0", "activo": "y",
    "contrasena": "SofiaLeon123*"}, follow_redirects=True)
check("Cuenta de Sofia Leon creada" in r.data.decode(), "el admin crea una cuenta con documento y telefono")
fila = crudo("sofia.leon@sgds.com")
check(fila.documento.startswith("gAAAAA") and fila.telefono.startswith("gAAAAA"),
      "quedan cifrados desde el alta")
with app.app_context():
    check(fila.documento_indice == indice_ciego("1012345678"), "con su indice ciego")
r = admin.post("/admin/usuarios/nuevo", data={
    "nombre": "Otra Persona", "correo": "otra.persona@sgds.com", "documento": " 1012345678 ",
    "rol": Rol.CONDUCTOR, "cliente_id": "0", "activo": "y"}, follow_redirects=True)
check("Ya existe una cuenta con este documento" in r.data.decode(),
      "un documento repetido se rechaza aunque este cifrado")
with app.app_context():
    try:
        db.session.add(Usuario(nombre="Directa", correo="directa@sgds.com", rol=Rol.CONDUCTOR,
                               documento="1012345678", contrasena_hash="x"))
        db.session.commit()
        check(False, "la base deberia rechazar el documento repetido")
    except IntegrityError:
        db.session.rollback()
        check(True, "la base tambien lo rechaza, por el indice unico del indice ciego")
html = admin.get("/admin/usuarios/?q=1012345678").data.decode()
check("sofia.leon@sgds.com" in html, "la busqueda encuentra el documento completo")
html = admin.get("/admin/usuarios/?q=10123").data.decode()
check("sofia.leon@sgds.com" not in html, "un fragmento del documento no lo encuentra")
html = admin.get("/admin/usuarios/?q=Sofia").data.decode()
check("sofia.leon@sgds.com" in html, "la busqueda por nombre sigue igual")

print("\n== 4. Edicion y auditoria enmascarada en pantalla ==")
id_sofia = usuario("sofia.leon@sgds.com").id
cifrado_antes = crudo("sofia.leon@sgds.com").telefono
r = admin.post(f"/admin/usuarios/{id_sofia}/editar", data={
    "nombre": "Sofia Leon", "correo": "sofia.leon@sgds.com", "documento": "1012345679",
    "telefono": "3019998877", "rol": Rol.CONDUCTOR, "cliente_id": "0", "activo": "y"},
    follow_redirects=True)
check("actualizada" in r.data.decode(), "el admin edita documento y telefono")
fila = crudo("sofia.leon@sgds.com")
check(fila.telefono != cifrado_antes and fila.telefono.startswith("gAAAAA"), "el telefono nuevo queda cifrado")
with app.app_context():
    check(fila.documento_indice == indice_ciego("1012345679"), "el indice ciego se actualiza")
u = usuario("sofia.leon@sgds.com")
check(u.documento == "1012345679" and u.telefono == "3019998877", "y se lee en claro")
with app.app_context():
    edicion = db.session.query(Auditoria).filter_by(accion="Editar usuario").order_by(Auditoria.id.desc()).first()
    detalle = edicion.detalle or ""
check("1012345679" not in detalle and "3019998877" not in detalle and "3015556677" not in detalle,
      "la auditoria no guarda el documento ni el telefono completos")
check("******5679" in detalle and "******8877" in detalle, "los guarda enmascarados")
html = admin.get("/admin/bitacora/").data.decode()
check("******8877" in html and "3019998877" not in html, "la bitacora los muestra enmascarados")
with app.app_context():
    registro = registrar_auditoria("Prueba", "cliente", "Cliente de prueba",
                                   {"telefono": ["3001112233", "3004445566"]}, avisar=False)
    db.session.commit()
    check("3004445566" in registro.detalle, "los datos de un cliente (empresa) no se enmascaran")
perfil = sesion("sofia.leon@sgds.com", "SofiaLeon123*")
perfil.post("/auth/perfil", data={"contrasena_actual": "SofiaLeon123*", "contrasena_nueva": "SofiaNueva123*"})
check("3019998877" in perfil.get("/auth/perfil").data.decode(), "el perfil muestra el telefono en claro")

print("\n== 5. Por voz: el resumen tampoco lo lee completo ==")
CLAVE = "clave-retell-prueba"
app.config.update(RETELL_API_KEY=CLAVE, MAKE_WEBHOOK_URL="", CORREO_OPERACIONES="")
retell = app.test_client()
with app.app_context():
    a = db.session.query(Usuario).filter_by(correo="admin@sgds.com").one()
    db.session.add(SesionAsistente(call_id="call_cifrado", usuario_id=a.id, rol=a.rol,
                                   vence_en=ahora() + VIGENCIA_SESION))
    db.session.commit()


def voz(args):
    cuerpo = json.dumps({"call": {"call_id": "call_cifrado"}, "name": "usuarios", "args": args},
                        ensure_ascii=False)
    r = retell.post("/api/asistente/admin/usuarios", data=cuerpo.encode(),
                    headers={"Content-Type": "application/json",
                             "X-Retell-Signature": symmetric["sign"](cuerpo, CLAVE)})
    return (r.get_json(silent=True) or {}).get("mensaje", "")


args = {"operacion": "editar", "usuario": "sofia", "telefono": "3017776655"}
resumen = voz(args)
check("uno terminado en 8877 a uno terminado en 6655" in resumen and "3017776655" not in resumen,
      f"el resumen dice solo los ultimos digitos: {resumen}")
final = voz({**args, "confirmar": True})
check(usuario("sofia.leon@sgds.com").telefono == "3017776655", f"y la edicion se aplica: {final}")
with app.app_context():
    por_voz = db.session.query(Auditoria).filter_by(call_id="call_cifrado").order_by(Auditoria.id.desc()).first()
check(por_voz is not None and "3017776655" not in (por_voz.detalle or "") + por_voz.resumen
      and "******6655" in por_voz.detalle, "la auditoria de voz los guarda enmascarados")

print("\n== 6. Con otra clave no se leen ==")
clave_correcta = app.config.get("CLAVE_CIFRADO", "")
app.config["CLAVE_CIFRADO"] = "otra-clave-distinta"
with app.app_context():
    try:
        db.session.query(Usuario).filter_by(correo="conductor1@sgds.com").one().documento
        check(False, "con otra clave deberia fallar")
    except ValueError as error:
        check("CLAVE_CIFRADO" in str(error), "con otra clave no se descifran, y el error explica por que")
    db.session.rollback()
app.config["CLAVE_CIFRADO"] = clave_correcta
check(usuario("conductor1@sgds.com").documento == "1023456789", "con la clave correcta vuelven a leerse")

print("\n== 7. Migracion m011 sobre una base con datos en claro ==")
from migraciones.m011_cifrado_datos_personales import INDICE, aplicar
with app.app_context():
    check(aplicar(verboso=False) == [], "sobre una base al dia no hace nada")
    with db.engine.begin() as conexion:
        conexion.execute(text(f"DROP INDEX {INDICE} ON usuarios"))
        conexion.execute(text("ALTER TABLE usuarios DROP COLUMN documento_indice"))
        conexion.execute(text("UPDATE usuarios SET documento = NULL, telefono = NULL"))
        conexion.execute(text("ALTER TABLE usuarios MODIFY documento VARCHAR(30) NULL, "
                              "MODIFY telefono VARCHAR(30) NULL"))
        conexion.execute(text("CREATE UNIQUE INDEX documento ON usuarios (documento)"))
        conexion.execute(text("UPDATE usuarios SET documento = '52345678', telefono = '3104567890' "
                              "WHERE correo = 'admin@sgds.com'"))
        conexion.execute(text("UPDATE usuarios SET documento = '80123456' WHERE correo = 'despachador@sgds.com'"))
    antes = db.session.execute(text("SELECT documento FROM usuarios WHERE correo = 'admin@sgds.com'"))
    check(antes.scalar() == "52345678", "la base queda como antes: datos en claro")
    db.session.remove()
    hecho = aplicar(verboso=False)
    check("2 cuenta(s) cifradas" in hecho and INDICE in hecho, f"amplia, cifra e indexa: {hecho}")
    check(aplicar(verboso=False) == [], "volver a ejecutarla no cambia nada")
    columnas = {c["name"]: c for c in inspect(db.engine).get_columns("usuarios")}
    check(columnas["documento"]["type"].length == 300 and columnas["telefono"]["type"].length == 300,
          "documento y telefono quedan en VARCHAR(300)")
    indices = inspect(db.engine).get_indexes("usuarios")
    check(not any(i["column_names"] == ["documento"] and i.get("unique") for i in indices),
          "el indice unico viejo sobre el documento se quita")
fila = crudo("admin@sgds.com")
check(fila.documento.startswith("gAAAAA") and fila.telefono.startswith("gAAAAA"),
      "los datos en claro quedan cifrados")
with app.app_context():
    check(fila.documento_indice == indice_ciego("52345678"), "con su indice ciego")
check(usuario("admin@sgds.com").documento == "52345678" and usuario("despachador@sgds.com").telefono is None,
      "la aplicacion los lee igual que antes")
r = sesion("admin@sgds.com", "Admin123*").post("/admin/usuarios/nuevo", data={
    "nombre": "Repetida", "correo": "repetida@sgds.com", "documento": "80123456",
    "rol": Rol.CONDUCTOR, "cliente_id": "0", "activo": "y"}, follow_redirects=True)
check("Ya existe una cuenta con este documento" in r.data.decode(), "la unicidad sigue tras migrar")


print("\n" + "=" * 55)
print("RESULTADO: " + ("TODAS LAS PRUEBAS PASARON" if not fallos else f"{len(fallos)} FALLAS"))
for f in fallos: print("   - " + f)
sys.exit(1 if fallos else 0)
