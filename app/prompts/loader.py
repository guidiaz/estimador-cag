"""Carga y renderizado de los prompts de estimación con Jinja2.

Cada versión de prompt vive en su propio directorio (`estimation/<version>/`)
con tres plantillas: `system.j2`, `user.j2` y `examples.j2` (esta última se
incluye desde `system.j2` con `{% include %}`).

`render_estimation_prompt(request, version="v1")` devuelve el par
`(system, user)` listo para enviar al modelo. Cambiar de versión es cambiar el
argumento `version`: no hay rutas ni nombres de plantilla codificados fuera de
este módulo, de modo que el resto del código no necesita tocarse.
"""

import re
from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from app.schemas import EstimationRequest

# Anclamos al directorio de este módulo (no al CWD): uvicorn y streamlit arrancan
# desde directorios distintos, así que solo `__file__` resuelve de forma robusta.
_ESTIMATION_DIR = Path(__file__).parent / "estimation"

# `version` puede venir de un query param (entrada no confiable) y se usa como
# nombre de directorio. Validación de entrada / defensa en profundidad: solo
# nombres simples, sin separadores de ruta ni `..`, antes de tocar el sistema de
# ficheros.
_VERSION_RE = re.compile(r"^[A-Za-z0-9_-]+$")


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
    }
    system = env.get_template("system.j2").render(context)
    user = env.get_template("user.j2").render(context)
    return system, user
