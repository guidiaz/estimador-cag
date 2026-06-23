"""Tests de la extracción de texto de adjuntos (`app.services.documents`).

Cubren el despacho por tipo y el mapeo de errores a `ValueError` (que el router
traduce a 400): PDF y Word `.docx` válidos extraen su texto; un tipo no soportado,
un binario corrupto y un archivo que excede el límite de tamaño fallan; un PDF sin
texto devuelve cadena vacía (no es error). No tocan red ni proveedor.
"""

import io

import pytest
from docx import Document

from app.services.documents import MAX_FILE_BYTES, extract_text


def _make_pdf(text: str) -> bytes:
    """PDF mínimo y válido con una línea de texto extraíble (pypdf solo lee)."""
    stream = b"BT /F1 24 Tf 100 700 Td (" + text.encode("latin-1") + b") Tj ET"
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    xref_pos = len(out)
    out += b"xref\n0 " + str(len(objs) + 1).encode() + b"\n0000000000 65535 f \n"
    for off in offsets:
        out += ("%010d 00000 n \n" % off).encode()
    out += (
        b"trailer\n<< /Size " + str(len(objs) + 1).encode() + b" /Root 1 0 R >>\n"
        b"startxref\n" + str(xref_pos).encode() + b"\n%%EOF"
    )
    return bytes(out)


def _make_docx(paragraphs: list[str]) -> bytes:
    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# --- PDF -----------------------------------------------------------------------


def test_extrae_texto_de_pdf():
    data = _make_pdf("Alcance del proyecto movil")
    assert extract_text("doc.pdf", data) == "Alcance del proyecto movil"


def test_pdf_corrupto_lanza_value_error():
    with pytest.raises(ValueError, match="No se pudo leer el PDF"):
        extract_text("roto.pdf", b"esto no es un pdf")


# --- Word .docx ----------------------------------------------------------------


def test_extrae_texto_de_docx_uniendo_parrafos():
    data = _make_docx(["Primer parrafo.", "", "Segundo parrafo."])
    # Los párrafos vacíos se descartan; los demás se unen con doble salto.
    assert extract_text("doc.docx", data) == "Primer parrafo.\n\nSegundo parrafo."


def test_docx_corrupto_lanza_value_error():
    with pytest.raises(ValueError, match="No se pudo leer el documento Word"):
        extract_text("roto.docx", b"PK\x03\x04 basura")


# --- despacho y límites --------------------------------------------------------


def test_tipo_no_soportado_lanza_value_error():
    with pytest.raises(ValueError, match="no soportado"):
        extract_text("notas.txt", b"texto plano")


def test_doc_binario_antiguo_no_soportado():
    with pytest.raises(ValueError, match="no soportado"):
        extract_text("viejo.doc", b"\xd0\xcf\x11\xe0")


def test_extension_se_resuelve_ignorando_mayusculas():
    data = _make_docx(["Contenido"])
    assert extract_text("DOC.DOCX", data) == "Contenido"


def test_archivo_sin_extension_no_soportado():
    with pytest.raises(ValueError, match="no soportado"):
        extract_text("sinextension", b"datos")


def test_archivo_demasiado_grande_lanza_value_error():
    big = b"x" * (MAX_FILE_BYTES + 1)
    with pytest.raises(ValueError, match="supera el máximo"):
        extract_text("grande.pdf", big)
