"""Extracción de texto plano de documentos adjuntos (PDF / Word).

Convierte la documentación complementaria que el cliente adjunta a una estimación
(`POST /sessions/{id}/estimate`) en **texto plano**, en el propio backend, antes de
construir el prompt.

**Por qué extraer aquí y no delegar en la Files API del proveedor.** No subimos los
documentos a la Files API de OpenAI/Anthropic; sacamos el texto nosotros. Razones:

- **Independencia del proveedor.** El texto plano se inyecta como un mensaje más, así
  que funciona igual sea cual sea el `PRIMARY_MODEL`/`FALLBACK_MODEL` configurado y el
  fallback entre Anthropic y OpenAI no depende de que ambos soporten el mismo formato
  binario ni la misma API de archivos.
- **Control para el RAG futuro.** Tener el texto plano en el backend es el punto de
  partida natural para el *chunking* y la indexación de un RAG más adelante: troceamos,
  embebemos e indexamos sobre texto que ya controlamos, sin reextraer ni depender de un
  almacén de archivos remoto.
- **Coste y trazabilidad.** Evita reenviar el binario completo en cada turno y nos deja
  medir/loguear cuánto texto aportó cada adjunto (sin loguear su contenido).

Solo se soportan formatos basados en texto: PDF (`pypdf`) y Word moderno `.docx`
(`python-docx`). El `.doc` binario antiguo y los PDF escaneados (solo imagen, sin capa
de texto) quedan fuera: el primero se rechaza; el segundo extrae cadena vacía y el
llamante decide (avisar y continuar solo con la transcripción).
"""

from __future__ import annotations

import io

import pypdf
from docx import Document

# Cotas defensivas: `UploadFile.read()` carga los bytes en memoria, así que acotamos
# tamaño y número para que un adjunto enorme (o muchos) no tumbe al worker.
MAX_ATTACHMENTS = 8
MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB por archivo

_PDF_EXTS = (".pdf",)
_DOCX_EXTS = (".docx",)


def _suffix(filename: str) -> str:
    name = filename.lower().strip()
    dot = name.rfind(".")
    return name[dot:] if dot != -1 else ""


def _extract_pdf(data: bytes) -> str:
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            # Intento con contraseña vacía (PDFs «protegidos» sin clave real); si no
            # abre, es un fallo de negocio del adjunto, no del proveedor.
            try:
                reader.decrypt("")
            except Exception as exc:  # noqa: BLE001
                raise ValueError("El PDF está cifrado y no se puede leer") from exc
        pages = [page.extract_text() or "" for page in reader.pages]
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001 - pypdf lanza varios tipos ante PDF corrupto
        raise ValueError(f"No se pudo leer el PDF: {exc}") from exc
    return "\n\n".join(p.strip() for p in pages if p.strip()).strip()


def _extract_docx(data: bytes) -> str:
    try:
        document = Document(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001 - docx corrupto / no es un .docx real
        raise ValueError(f"No se pudo leer el documento Word: {exc}") from exc
    parts = [p.text.strip() for p in document.paragraphs if p.text.strip()]
    return "\n\n".join(parts).strip()


def extract_text(filename: str, data: bytes) -> str:
    """Devuelve el texto plano de un adjunto, despachando por extensión.

    Lanza `ValueError` (→ 400 en el router) ante un tipo no soportado, un archivo
    que excede `MAX_FILE_BYTES` o un binario corrupto/cifrado. Una extracción vacía
    (p. ej. PDF escaneado sin capa de texto) devuelve `""`: no es un error aquí, lo
    gestiona el llamante.
    """
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(
            f"El archivo {filename!r} supera el máximo de "
            f"{MAX_FILE_BYTES // (1024 * 1024)} MB"
        )

    ext = _suffix(filename)
    if ext in _PDF_EXTS:
        return _extract_pdf(data)
    if ext in _DOCX_EXTS:
        return _extract_docx(data)
    raise ValueError(
        f"Tipo de archivo no soportado: {filename!r}. "
        "Solo se admiten PDF (.pdf) y Word moderno (.docx)."
    )
