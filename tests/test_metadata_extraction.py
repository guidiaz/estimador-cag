"""Tests de la extracción de metadata de proyecto (`extract_project_metadata`).

La segunda llamada por turno se mockea sustituyendo `_complete_messages` (no se
llama al proveedor). Se verifica: el JSON se funde sobre los hechos actuales
(escalar nuevo gana, `null` no pisa, listas se **unen**), se toleran las vallas
```json, y cualquier fallo (no-JSON, error del proveedor, validación) degrada
devolviendo la metadata sin cambios.
"""

from app.services import llm_service
from app.services.llm_service import EstimationResult, extract_project_metadata
from app.services.sessions import ProjectMetadata


def _result(text: str) -> EstimationResult:
    return EstimationResult(estimation=text, model="m", provider="p", used_tokens=1)


def _patch_completion(monkeypatch, text: str) -> None:
    monkeypatch.setattr(
        llm_service,
        "_complete_messages",
        lambda messages, max_tokens=1024: _result(text),
    )


def test_funde_hechos_uniendo_listas_y_respetando_null(monkeypatch):
    current = ProjectMetadata(project_name="Acme", mentioned_technologies=["React"])
    _patch_completion(
        monkeypatch,
        '{"project_name": null, "assumed_team_size": 4, '
        '"mentioned_technologies": ["React", "Postgres"], '
        '"agreed_scope": "MVP en 8 semanas"}',
    )

    out = extract_project_metadata(current, "usuario", "estimación")

    assert out.project_name == "Acme"  # null no sobreescribe lo conocido
    assert out.assumed_team_size == 4  # hecho nuevo
    assert out.mentioned_technologies == ["React", "Postgres"]  # unión, sin duplicar
    assert out.agreed_scope == "MVP en 8 semanas"


def test_tolera_vallas_de_codigo(monkeypatch):
    _patch_completion(monkeypatch, '```json\n{"project_name": "Beta"}\n```')
    out = extract_project_metadata(ProjectMetadata(), "u", "a")
    assert out.project_name == "Beta"


def test_respuesta_no_json_devuelve_metadata_sin_cambios(monkeypatch):
    current = ProjectMetadata(project_name="Acme")
    _patch_completion(monkeypatch, "lo siento, no puedo ayudar con eso")
    assert extract_project_metadata(current, "u", "a") == current


def test_validacion_fallida_devuelve_metadata_sin_cambios(monkeypatch):
    current = ProjectMetadata(project_name="Acme")
    # assumed_team_size fuera de rango (ge=1, le=100) → ValidationError → swallow.
    _patch_completion(monkeypatch, '{"assumed_team_size": 9999}')
    assert extract_project_metadata(current, "u", "a") == current


def test_error_del_proveedor_devuelve_metadata_sin_cambios(monkeypatch):
    def _boom(messages, max_tokens=1024):
        raise RuntimeError("proveedor caído")

    monkeypatch.setattr(llm_service, "_complete_messages", _boom)
    current = ProjectMetadata(project_name="Acme")
    assert extract_project_metadata(current, "u", "a") == current
