"""Tests del system prompt de sesión (`render_session_system_prompt`).

Verifican el bloque `<project_metadata>`: vacío en la primera llamada (sin hechos)
y poblado con los campos conocidos después, omitiendo los vacíos. El render es
schema-driven (recorre `model_fields`), así que no se comprueban nombres
codificados sino el comportamiento. No tocan red ni proveedor.
"""

import re

from app.prompts.loader import render_session_system_prompt
from app.services.sessions import ProjectMetadata


def _block(prompt: str) -> str:
    match = re.search(r"<project_metadata>(.*?)</project_metadata>", prompt, re.S)
    assert match is not None, "el prompt debe contener el bloque <project_metadata>"
    return match.group(1).strip()


def test_bloque_vacio_en_la_primera_llamada():
    prompt = render_session_system_prompt(ProjectMetadata())
    assert _block(prompt) == ""


def test_bloque_lista_los_hechos_conocidos_y_omite_los_vacios():
    metadata = ProjectMetadata(
        project_name="Acme CRM",
        assumed_team_size=4,
        mentioned_technologies=["React", "FastAPI"],
    )
    block = _block(render_session_system_prompt(metadata))

    assert "project_name: Acme CRM" in block
    assert "assumed_team_size: 4" in block
    # Las listas se renderizan unidas por comas.
    assert "mentioned_technologies: React, FastAPI" in block
    # `agreed_scope` está vacío → no aparece.
    assert "agreed_scope" not in block


def test_el_prompt_incluye_los_ejemplos_cag():
    prompt = render_session_system_prompt(ProjectMetadata())
    assert "### Ejemplo 1" in prompt
