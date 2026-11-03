"""RNF5 - Revision del contenido real de los archivos que se suben.

La extension del nombre y el tipo MIME los escribe quien sube el archivo:
renombrar un ejecutable a .pdf o .csv no lo convierte en eso. Antes de guardar
o procesar un archivo se revisa su contenido por su firma, los primeros bytes
que identifican cada formato ("numeros magicos"), y se rechaza todo lo que no
sea texto: programas, documentos, comprimidos, imagenes y cualquier binario.

Hoy el unico modulo de carga es la importacion de pedidos, que solo admite
CSV, es decir texto. Sin esta revision, el importador decodificaba cualquier
binario como latin-1 (que acepta todos los bytes) y lo pasaba al lector CSV.
"""

# (firma, que es, si es un programa). Gana la primera que coincide.
FIRMAS = (
    (b"MZ", "un programa ejecutable de Windows (.exe o .dll)", True),
    (b"\x7fELF", "un programa ejecutable de Linux", True),
    (b"\xcf\xfa\xed\xfe", "un programa ejecutable de macOS", True),
    (b"\xce\xfa\xed\xfe", "un programa ejecutable de macOS", True),
    (b"\xca\xfe\xba\xbe", "un programa ejecutable de macOS o de Java", True),
    (b"#!/", "un script ejecutable", True),
    (b"%PDF", "un documento PDF", False),
    (b"PK\x03\x04", "un archivo comprimido (ZIP, o un Excel o Word .xlsx o .docx)", False),
    (b"\xd0\xcf\x11\xe0", "un documento de Office (.xls o .doc)", False),
    (b"Rar!", "un archivo comprimido RAR", False),
    (b"7z\xbc\xaf\x27\x1c", "un archivo comprimido 7z", False),
    (b"\x1f\x8b", "un archivo comprimido gzip", False),
    (b"\x89PNG", "una imagen PNG", False),
    (b"\xff\xd8\xff", "una imagen JPEG", False),
    (b"GIF8", "una imagen GIF", False),
    (b"\xff\xfe", "un texto en UTF-16; guárdelo como CSV UTF-8", False),
    (b"\xfe\xff", "un texto en UTF-16; guárdelo como CSV UTF-8", False),
)


class ArchivoRechazado(Exception):
    """El contenido no es texto. `contenido` dice que es; `es_programa`, si ejecuta codigo."""

    def __init__(self, contenido, es_programa):
        self.contenido = contenido
        self.es_programa = es_programa
        super().__init__(contenido)


def contenido_real(datos):
    """(que es, es_programa) si el archivo no es texto; None si lo es."""
    for firma, descripcion, es_programa in FIRMAS:
        if datos.startswith(firma):
            # "MZ" tambien puede abrir un texto; un ejecutable real trae bytes
            # nulos en su cabecera.
            if firma == b"MZ" and b"\x00" not in datos[:1024]:
                continue
            return descripcion, es_programa
    if b"\x00" in datos:
        return "un archivo binario, no de texto", False
    return None


def exigir_texto(datos):
    """Lanza ArchivoRechazado si `datos` no es un archivo de texto."""
    tipo = contenido_real(datos)
    if tipo is not None:
        raise ArchivoRechazado(*tipo)
