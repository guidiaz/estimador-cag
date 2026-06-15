"""Tests del template de prompt de estimación v1.

Verifican el resultado de `render_estimation_prompt` (puramente local: no toca
APIs externas ni al modelo, corre en milisegundos). Comprueban el contrato
estructural de las plantillas Jinja2, no la calidad de la respuesta del LLM.

Nota sobre los marcadores: `examples.j2` se incluye siempre, así que el render
del `system` contiene los ejemplos few-shot (tablas, «Supuestos:», «Fase»,
«Horas», `|`…). Por eso las aserciones apuntan a frases de instrucción únicas de
cada rama condicional, verificadas como ausentes de los ejemplos y del resto del
template, en lugar de a palabras sueltas que también aparecen en los ejemplos.
"""

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


def test_description_va_dentro_del_bloque_project_description():
    # Sentinela > 20 chars (min_length del contrato) y con `<`/`&` para fijar de
    # paso que autoescape=False: el texto debe sobrevivir verbatim.
    sentinel = "Proyecto <Acme> con SLA 99.9% & panel de control en tiempo real"
    _, user = render_estimation_prompt(_make_request(description=sentinel))

    assert "<project_description>" in user
    assert "</project_description>" in user

    # El contenido debe ir DENTRO del bloque, no filtrado en cualquier sitio.
    inner = user.split("<project_description>", 1)[1].split("</project_description>", 1)[0]
    assert sentinel in inner


def test_output_format_cambia_la_directiva_de_formato():
    system_table, _ = render_estimation_prompt(
        _make_request(output_format=OutputFormat.PHASES_TABLE)
    )
    system_narrative, _ = render_estimation_prompt(
        _make_request(output_format=OutputFormat.NARRATIVE)
    )

    # phases_table: directiva de tabla presente; ausente en narrative.
    assert "tabla Markdown de fases" in system_table
    assert "tabla Markdown de fases" not in system_narrative

    # narrative: directiva de prosa presente; ausente en phases_table.
    assert "sin tablas ni listas" in system_narrative
    assert "sin tablas ni listas" not in system_table


def test_detail_level_detailed_incluye_asunciones_por_fase():
    system_detailed, _ = render_estimation_prompt(
        _make_request(detail_level=DetailLevel.DETAILED)
    )
    system_summary, _ = render_estimation_prompt(
        _make_request(detail_level=DetailLevel.SUMMARY)
    )

    # La instrucción extra de asunciones por fase solo aparece en `detailed`.
    # ("asunciones" no aparece en examples.j2, que usa "Supuestos": sin colisión.)
    assert "asunciones por fase" in system_detailed
    assert "asunciones por fase" not in system_summary
