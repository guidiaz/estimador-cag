"""Tests del cargador de prompts (`app.prompts.loader`), independientes de versión.

Cubren la validación del argumento `version`, que es la pieza relevante para la
seguridad: `version` puede llegar desde un query param (`?prompt_version=`) y se
usa como nombre de directorio bajo `estimation/`. La defensa en profundidad
(`_VERSION_RE` + comprobación de directorio) debe rechazar tanto los intentos de
recorrido de rutas como las versiones inexistentes, siempre con `ValueError`
(que el router traduce a 400). Son tests puramente locales: no tocan el modelo.
"""

import hashlib

import pytest
import structlog

from app.prompts.loader import render_estimation_prompt
from app.schemas import (
    DetailLevel,
    EstimationRequest,
    OutputFormat,
    ProjectType,
    ReferenceProject,
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


# --- Logging del render -------------------------------------------------------


def _capture_render(**overrides):
    """Renderiza capturando los eventos de structlog. Devuelve (system, user, evento)."""
    with structlog.testing.capture_logs() as logs:
        system, user = render_estimation_prompt(_make_request(**overrides))
    events = [entry for entry in logs if entry["event"] == "prompt.rendered"]
    assert len(events) == 1, f"se esperaba 1 evento prompt.rendered, hubo {len(events)}"
    return system, user, events[0]


def test_render_emite_evento_con_version_y_hashes_del_contenido():
    system, user, event = _capture_render()

    assert event["log_level"] == "info"
    assert event["prompt_version"] == "v1"
    # Los hashes se verifican contra sha256 calculado de forma independiente, no
    # contra el helper interno: comprueban el valor real logueado.
    assert event["system_hash"] == hashlib.sha256(system.encode("utf-8")).hexdigest()
    assert event["user_hash"] == hashlib.sha256(user.encode("utf-8")).hexdigest()
    assert event["reference_count"] == 0


def test_evento_no_filtra_contenido_del_prompt():
    # Regla del proyecto: solo metadatos, nunca la descripción ni las referencias.
    sentinel = "DESCRIPCION-SECRETA-QUE-NO-DEBE-APARECER-EN-LOGS"
    _, _, event = _capture_render(description=f"{sentinel} con detalle suficiente.")
    assert sentinel not in repr(event)


def test_evento_loguea_la_version_normalizada():
    # `V1` se normaliza a `v1`; el evento debe reflejar la versión efectiva.
    with structlog.testing.capture_logs() as logs:
        render_estimation_prompt(_make_request(), version="V1")
    event = next(e for e in logs if e["event"] == "prompt.rendered")
    assert event["prompt_version"] == "v1"


def test_reference_count_refleja_las_referencias_aportadas():
    refs = [
        ReferenceProject(name="Uno", description="Descripción suficientemente larga."),
        ReferenceProject(name="Dos", description="Otra descripción bien larga."),
    ]
    _, _, event = _capture_render(reference_projects=refs)
    assert event["reference_count"] == 2
