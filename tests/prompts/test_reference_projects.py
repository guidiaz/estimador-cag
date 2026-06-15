"""Tests del bloque de proyectos de referencia en los prompts de estimación.

El campo `reference_projects` vive en el contrato compartido `EstimationRequest`,
así que ambas versiones de plantilla (v1 y v2) deben recorrerlo con `{% for %}`
cuando está presente y omitir la sección cuando no. Por eso los tests se
parametrizan sobre las dos versiones: comparten el mismo contrato de render.

El test del camino `None` es el más importante: con `StrictUndefined`, soltar un
`{% if reference_projects %}` sin pasar la variable reventaría *todos* los
renders de esa versión. Verifica a la vez que no lanza y que no hay sección.
"""

import pytest
from pydantic import ValidationError

from app.prompts.loader import render_estimation_prompt
from app.schemas import (
    DetailLevel,
    EstimationRequest,
    OutputFormat,
    ProjectType,
    ReferenceProject,
)

_VERSIONS = ["v1", "v2"]
_SECTION_HEADER = "## Proyectos de referencia aportados por el cliente"


def _make_request(reference_projects=None, **overrides) -> EstimationRequest:
    data = {
        "description": (
            "App móvil de reservas para clínicas con agenda compartida y pagos."
        ),
        "project_type": ProjectType.MOBILE_APP,
        "detail_level": DetailLevel.MEDIUM,
        "output_format": OutputFormat.PHASES_TABLE,
        "reference_projects": reference_projects,
    }
    data.update(overrides)
    return EstimationRequest(**data)


@pytest.mark.parametrize("version", _VERSIONS)
def test_sin_referencias_no_lanza_ni_emite_seccion(version):
    # Regresión de StrictUndefined: con reference_projects=None el render debe
    # completarse y NO incluir la sección de referencias.
    system, _ = render_estimation_prompt(_make_request(None), version=version)
    assert _SECTION_HEADER not in system


@pytest.mark.parametrize("version", _VERSIONS)
def test_lista_vacia_se_comporta_como_sin_referencias(version):
    # `[]` es falsy en el `{% if %}`: misma salida que None, sin sección.
    system, _ = render_estimation_prompt(_make_request([]), version=version)
    assert _SECTION_HEADER not in system


@pytest.mark.parametrize("version", _VERSIONS)
def test_recorre_todas_las_referencias_con_su_indice(version):
    refs = [
        ReferenceProject(
            name="Gimnasios Reservas",
            description="App iOS/Android de reservas de clases con pagos.",
            total_hours=670,
            duration="10 semanas",
            notes="El back-office se comió el doble de lo previsto.",
        ),
        ReferenceProject(
            name="Clínica Citas",
            description="Agenda compartida multi-profesional, sin pagos.",
        ),
    ]
    system, _ = render_estimation_prompt(_make_request(refs), version=version)

    assert _SECTION_HEADER in system
    # Ambas referencias presentes, numeradas por loop.index.
    assert "Referencia 1: Gimnasios Reservas" in system
    assert "Referencia 2: Clínica Citas" in system
    assert "App iOS/Android de reservas de clases con pagos." in system
    assert "Agenda compartida multi-profesional, sin pagos." in system
    # Campos opcionales de la primera referencia.
    assert "Horas reales: 670 h" in system
    assert "Duración real: 10 semanas" in system
    assert "El back-office se comió el doble de lo previsto." in system


@pytest.mark.parametrize("version", _VERSIONS)
def test_campos_opcionales_ausentes_no_emiten_linea(version):
    # Una referencia con solo name/description no debe arrastrar las líneas de
    # horas/duración/notas (usamos una única referencia para aislar la aserción).
    refs = [
        ReferenceProject(
            name="Clínica Citas",
            description="Agenda compartida multi-profesional, sin pagos.",
        )
    ]
    system, _ = render_estimation_prompt(_make_request(refs), version=version)

    assert _SECTION_HEADER in system
    assert "Referencia 1: Clínica Citas" in system
    assert "Horas reales" not in system
    assert "Duración real" not in system
    assert "Notas:" not in system


def test_el_contrato_acota_el_numero_de_referencias():
    # Cap del contrato (max_length=8): protege el tamaño del prompt. >8 es 422 en
    # el endpoint vía ValidationError aquí.
    refs = [
        ReferenceProject(name=f"Proyecto {i}", description="Descripción de referencia válida.")
        for i in range(9)
    ]
    with pytest.raises(ValidationError):
        _make_request(refs)
