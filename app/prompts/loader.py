"""Carga y renderizado de los prompts de estimación con Jinja2.

Cada versión de prompt vive en su propio directorio (`estimation/<version>/`)
con tres plantillas: `system.j2`, `user.j2` y `examples.j2` (esta última se
incluye desde `system.j2` con `{% include %}`).

`render_estimation_prompt(request, version="v1")` devuelve el par
`(system, user)` listo para enviar al modelo. Cambiar de versión es cambiar el
argumento `version`: no hay rutas ni nombres de plantilla codificados fuera de
este módulo, de modo que el resto del código no necesita tocarse.
"""

import hashlib
import re
from functools import lru_cache
from pathlib import Path

import structlog
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from app.context.examples import ESTIMATION_EXAMPLES
from app.schemas import EstimationRequest
from app.services.sessions import ProjectMetadata

logger = structlog.get_logger("app.prompts")

# Anclamos al directorio de este módulo (no al CWD): uvicorn y streamlit arrancan
# desde directorios distintos, así que solo `__file__` resuelve de forma robusta.
_ESTIMATION_DIR = Path(__file__).parent / "estimation"
_SESSION_DIR = Path(__file__).parent / "session"

# `version` puede venir de un query param (entrada no confiable) y se usa como
# nombre de directorio. Validación de entrada / defensa en profundidad: solo
# nombres simples, sin separadores de ruta ni `..`, antes de tocar el sistema de
# ficheros.
_VERSION_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _content_hash(content: str) -> str:
    """sha256 hexdigest del texto renderizado.

    Mismo cálculo que `_system_prompt_hash` del servicio (no se importa para no
    crear un ciclo `loader` ↔ `llm_service`), de modo que el `system_hash` que se
    loguea correlaciona con el `sp_hash` que el servicio usa en la clave de cache.
    """
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@lru_cache(maxsize=None)
def _environment(version: str) -> Environment:
    """Environment de Jinja2 con la raíz en `estimation/<version>/`.

    Con la raíz en el directorio de la versión, tanto
    `get_template("system.j2")` como `{% include "examples.j2" %}` usan nombres
    simples y cambiar de versión no obliga a editar ninguna plantilla.
    """
    if not _VERSION_RE.fullmatch(version):
        raise ValueError(f"Versión de prompt no válida: {version!r}")
    version_dir = _ESTIMATION_DIR / version
    if not version_dir.is_dir():
        raise ValueError(f"Versión de prompt desconocida: {version!r}")
    return Environment(
        loader=FileSystemLoader(version_dir),
        undefined=StrictUndefined,  # cualquier variable no provista revienta
        trim_blocks=True,
        lstrip_blocks=True,
        autoescape=False,  # generamos texto/Markdown para el modelo, no HTML
    )


def render_estimation_prompt(
    request: EstimationRequest, version: str = "v1"
) -> tuple[str, str]:
    """Renderiza `(system, user)` para una petición de estimación.

    Los enums se pasan por su `.value` (string) para que tanto la interpolación
    `{{ ... }}` como las comparaciones `{% if ... %}` de las plantillas operen
    sobre el valor del contrato y no sobre el repr del enum.

    `version` se normaliza a minúsculas (los directorios de versión lo son), de
    modo que `V2` resuelve a `v2` de forma idéntica en cualquier sistema de
    ficheros, sea o no sensible a mayúsculas.
    """
    version = version.lower()
    env = _environment(version)
    context = {
        "description": request.description,
        "project_type": request.project_type.value,
        "detail_level": request.detail_level.value,
        "output_format": request.output_format.value,
        # Siempre presente (aunque sea None): con StrictUndefined, un
        # `{% if reference_projects %}` reventaría si la variable no existiera.
        # Se pasan los objetos ReferenceProject; las plantillas acceden a sus
        # atributos (`rp.name`, `rp.total_hours`, …).
        "reference_projects": request.reference_projects,
    }
    system = env.get_template("system.j2").render(context)
    user = env.get_template("user.j2").render(context)

    # Evento de depuración para producción: identifica de forma unívoca el prompt
    # generado SIN volcar su contenido (la descripción y las referencias del
    # cliente no se loguean, solo sus hashes). Permite responder «¿qué plantilla y
    # qué entrada exactas produjeron esta respuesta?». `system_hash` coincide con
    # el `sp_hash` de la clave de cache; `user_hash` cambia solo con la
    # descripción. `reference_count` es metadato seguro del nuevo campo.
    logger.info(
        "prompt.rendered",
        prompt_version=version,
        system_hash=_content_hash(system),
        user_hash=_content_hash(user),
        reference_count=len(request.reference_projects or []),
    )

    return system, user


@lru_cache(maxsize=None)
def _session_environment(version: str) -> Environment:
    """Environment Jinja2 para el system prompt de sesión (`session/<version>/`).

    A diferencia de `render_estimation_prompt`, `version` aquí no viene de entrada
    de usuario (lo fija el backend), así que se omite la validación antirrecorrido
    de rutas; basta comprobar que el directorio existe."""
    version_dir = _SESSION_DIR / version
    if not version_dir.is_dir():
        raise ValueError(f"Versión de prompt de sesión desconocida: {version!r}")
    return Environment(
        loader=FileSystemLoader(version_dir),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        autoescape=False,
    )


def _metadata_facts(metadata: ProjectMetadata) -> list[dict]:
    """Convierte la metadata en pares `{name, value}` para el bloque, omitiendo
    los campos vacíos. Recorre `model_fields` (no nombres codificados): añadir un
    campo a `ProjectMetadata` aparece en el bloque sin tocar esta función ni la
    plantilla. Las claves son los nombres de campo del modelo, los mismos que
    produce la extracción, para que el modelo vea una nomenclatura consistente."""
    facts: list[dict] = []
    dumped = metadata.model_dump()
    for name in type(metadata).model_fields:
        value = dumped[name]
        if value in (None, "", []):
            continue
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value)
        facts.append({"name": name, "value": value})
    return facts


def render_session_system_prompt(
    metadata: ProjectMetadata, version: str = "v1"
) -> str:
    """Renderiza el system prompt del path de sesión con el bloque
    `<project_metadata>`.

    El bloque lista los hechos ya conocidos del proyecto; si `metadata` está vacía
    (primera llamada de la sesión), el bloque se renderiza vacío. Incluye además los
    ejemplos CAG (`ESTIMATION_EXAMPLES`) como referencia de estilo."""
    env = _session_environment(version)
    facts = _metadata_facts(metadata)
    system = env.get_template("system.j2").render(
        facts=facts, examples=ESTIMATION_EXAMPLES
    )
    logger.info(
        "session.prompt.rendered",
        prompt_version=version,
        system_hash=_content_hash(system),
        known_fields=[f["name"] for f in facts],
    )
    return system
