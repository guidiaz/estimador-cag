"""Tests del template de prompt de estimación v2.

Verifican el resultado de `render_estimation_prompt(..., version="v2")` (puramente
local: no toca APIs externas ni al modelo). Comprueban el contrato estructural
de las plantillas Jinja2, no la calidad de la respuesta del LLM.

A diferencia de v1, estas aserciones apuntan al *valor añadido propio de v2* —la
línea de cierre «Confianza:», el «Colchón recomendado» y el «riesgo principal»—,
no a frases que v2 comparte con v1 (que pasarían por el motivo equivocado). El
marcador «Confianza:» es además un invariante: v2 lo cierra SIEMPRE, en toda
rama de `detail_level` y `output_format`.
"""

import pytest

from app.prompts.loader import render_estimation_prompt
from app.schemas import (
    DetailLevel,
    EstimationRequest,
    OutputFormat,
    ProjectType,
)

_VERSION = "v2"


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


def _render_system(**overrides) -> str:
    system, _ = render_estimation_prompt(_make_request(**overrides), version=_VERSION)
    return system


def test_description_va_dentro_del_bloque_project_description():
    # Sentinela > 20 chars (min_length del contrato) y con `<`/`&` para fijar de
    # paso que autoescape=False: el texto debe sobrevivir verbatim.
    sentinel = "Proyecto <Acme> con SLA 99.9% & panel de control en tiempo real"
    _, user = render_estimation_prompt(
        _make_request(description=sentinel), version=_VERSION
    )

    assert "<project_description>" in user
    assert "</project_description>" in user

    inner = user.split("<project_description>", 1)[1].split(
        "</project_description>", 1
    )[0]
    assert sentinel in inner


def test_cierra_siempre_con_la_linea_de_confianza():
    # «Confianza:» es la firma de v2: el cierre es incondicional, presente en
    # cualquier combinación de nivel de detalle y formato de salida.
    for detail in DetailLevel:
        for fmt in OutputFormat:
            system = _render_system(detail_level=detail, output_format=fmt)
            assert "Confianza:" in system, (detail, fmt)


def test_output_format_cambia_la_directiva_de_formato():
    system_table = _render_system(output_format=OutputFormat.PHASES_TABLE)
    system_narrative = _render_system(output_format=OutputFormat.NARRATIVE)

    # «fila de totales» es directiva propia de la rama phases_table; ni la rama
    # narrativa ni los ejemplos (incluidos en todo render) usan esa frase, así
    # que aísla la condición de formato sin colisionar con «Colchón recomendado»,
    # que sí aparece en los ejemplos de cualquier render.
    assert "fila de totales" in system_table
    assert "fila de totales" not in system_narrative

    # La directiva de prosa de v2 es única de la rama narrativa.
    assert "sin tablas ni listas" in system_narrative
    assert "sin tablas ni listas" not in system_table


def test_detail_level_detailed_pide_riesgo_principal_por_fase():
    system_detailed = _render_system(detail_level=DetailLevel.DETAILED)
    system_summary = _render_system(detail_level=DetailLevel.SUMMARY)

    # v2 (a diferencia de v1) pide en `detailed` asunciones Y riesgo por fase.
    assert "las asunciones y el riesgo principal" in system_detailed
    assert "las asunciones y el riesgo principal" not in system_summary


def test_project_type_se_refleja_en_el_encabezado():
    system_mobile = _render_system(project_type=ProjectType.MOBILE_APP)
    system_pipeline = _render_system(project_type=ProjectType.DATA_PIPELINE)

    assert "una aplicación móvil" in system_mobile
    assert "un pipeline de datos" in system_pipeline
