"""Tests del cargador de prompts (`app.prompts.loader`), independientes de versión.

Cubren la validación del argumento `version`, que es la pieza relevante para la
seguridad: `version` puede llegar desde un query param (`?prompt_version=`) y se
usa como nombre de directorio bajo `estimation/`. La defensa en profundidad
(`_VERSION_RE` + comprobación de directorio) debe rechazar tanto los intentos de
recorrido de rutas como las versiones inexistentes, siempre con `ValueError`
(que el router traduce a 400). Son tests puramente locales: no tocan el modelo.
"""

import pytest

from app.prompts.loader import render_estimation_prompt
from app.schemas import (
    DetailLevel,
    EstimationRequest,
    OutputFormat,
    ProjectType,
)


def _make_request(**overrides) -> EstimationRequest:
    """EstimationRequest base válido; los tests sobreescriben lo que les interesa."""
    data = {
        "description": (
            "App móvil de reservas para clínicas con agenda compartida y pagos."
        ),
        "project_type": ProjectType.MOBILE_APP,
        "detail_level": DetailLevel.MEDIUM,
        "output_format": OutputFormat.PHASES_TABLE,
    }
    data.update(overrides)
    return EstimationRequest(**data)


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_versiones_existentes_renderizan(version):
    # Las versiones reales presentes en disco devuelven el par (system, user).
    system, user = render_estimation_prompt(_make_request(), version=version)
    assert system and user


@pytest.mark.parametrize("version", ["V1", "V2"])
def test_version_en_mayusculas_se_normaliza(version):
    # `V2` debe renderizar igual que `v2` en cualquier SO (normalización a
    # minúsculas), no depender de un FS case-insensitive.
    upper_system, _ = render_estimation_prompt(_make_request(), version=version)
    lower_system, _ = render_estimation_prompt(
        _make_request(), version=version.lower()
    )
    assert upper_system == lower_system


@pytest.mark.parametrize(
    "version",
    [
        "../v1",  # recorrido hacia arriba
        "v1/..",  # separador de ruta embebido
        "v1/system",  # subruta con separador
        "..",  # el propio directorio padre
        "",  # cadena vacía
        "v 1",  # espacio: no casa con _VERSION_RE
        "v1.",  # punto: no casa con _VERSION_RE
    ],
)
def test_version_con_nombre_no_valido_lanza_valueerror(version):
    # Nombres con separadores de ruta, `..` o caracteres fuera de [A-Za-z0-9_-]
    # se rechazan ANTES de tocar el sistema de ficheros.
    with pytest.raises(ValueError):
        render_estimation_prompt(_make_request(), version=version)


def test_version_inexistente_lanza_valueerror():
    # Nombre con forma válida pero sin directorio correspondiente.
    with pytest.raises(ValueError):
        render_estimation_prompt(_make_request(), version="v999")
