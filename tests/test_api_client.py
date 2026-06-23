"""Tests del cliente HTTP de la UI (`api_client`).

Cubren la lógica pura y propensa a errores que conecta el editor de Streamlit con
el contrato de la API: `build_reference_projects` (filas del `data_editor` →
payload `reference_projects`) y la inclusión condicional del campo en
`request_estimation`. El renderizado del widget no se testea aquí (requiere un
run vivo de Streamlit); sí su transformación, que es donde están los casos límite
(`670.0`→int, celdas en blanco, filas parciales, mínimos del contrato).
"""

import api_client
from api_client import build_reference_projects, request_estimation


def _row(**overrides) -> dict:
    """Fila del editor con todas las columnas; los tests sobreescriben lo suyo."""
    base = {
        "name": None,
        "description": None,
        "total_hours": None,
        "duration": None,
        "notes": None,
    }
    base.update(overrides)
    return base


# --- build_reference_projects -------------------------------------------------


def test_fila_sembrada_vacia_no_produce_referencias():
    refs, problems = build_reference_projects([_row()])
    assert refs == []
    assert problems == []


def test_fila_completa_se_mapea_con_horas_enteras():
    refs, problems = build_reference_projects(
        [
            _row(
                name="Gimnasios Reservas",
                description="App iOS/Android de reservas con pagos.",
                total_hours=670.0,  # NumberColumn puede devolver float
                duration="10 semanas",
                notes="El back-office se comió el doble.",
            )
        ]
    )
    assert problems == []
    assert refs == [
        {
            "name": "Gimnasios Reservas",
            "description": "App iOS/Android de reservas con pagos.",
            "total_hours": 670,  # convertido a int
            "duration": "10 semanas",
            "notes": "El back-office se comió el doble.",
        }
    ]
    assert isinstance(refs[0]["total_hours"], int)


def test_fila_minima_omite_los_campos_opcionales_en_blanco():
    refs, problems = build_reference_projects(
        [
            _row(
                name="Clínica Citas",
                description="Agenda compartida multi-profesional.",
                duration="   ",  # solo espacios → omitido
            )
        ]
    )
    assert problems == []
    assert refs == [
        {
            "name": "Clínica Citas",
            "description": "Agenda compartida multi-profesional.",
        }
    ]


def test_horas_none_o_nan_se_omiten():
    refs, _ = build_reference_projects(
        [
            _row(name="Uno", description="Descripción suficientemente larga.", total_hours=None),
            _row(name="Dos", description="Otra descripción bien larga.", total_hours=float("nan")),
        ]
    )
    assert all("total_hours" not in ref for ref in refs)


def test_nombre_corto_es_problema_y_no_se_envia():
    refs, problems = build_reference_projects(
        [_row(name="ab", description="Descripción suficientemente larga.")]
    )
    assert refs == []
    assert len(problems) == 1
    assert "nombre" in problems[0]


def test_descripcion_corta_es_problema_y_no_se_envia():
    refs, problems = build_reference_projects(
        [_row(name="Proyecto válido", description="corta")]
    )
    assert refs == []
    assert len(problems) == 1
    assert "descripción" in problems[0]


def test_fila_solo_con_espacios_se_ignora_sin_problema():
    refs, problems = build_reference_projects(
        [_row(name="   ", description="   ", notes="  ")]
    )
    assert refs == []
    assert problems == []


def test_preserva_el_orden_y_mezcla_validas_con_invalidas():
    refs, problems = build_reference_projects(
        [
            _row(name="Primero", description="Descripción larga del primero."),
            _row(name=" x", description="Descripción larga pero nombre corto."),
            _row(name="Tercero", description="Descripción larga del tercero."),
        ]
    )
    assert [ref["name"] for ref in refs] == ["Primero", "Tercero"]
    assert len(problems) == 1


# --- request_estimation payload ----------------------------------------------


class _FakeResponse:
    is_error = False

    def json(self) -> dict:
        return {"text": "ok", "prompt_version": "v1"}


def _capture_post(monkeypatch) -> dict:
    """Sustituye httpx.post y devuelve un dict que recoge el último payload."""
    captured: dict = {}

    def fake_post(url, json, timeout):  # noqa: A002 - firma de httpx.post
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse()

    monkeypatch.setattr(api_client.httpx, "post", fake_post)
    return captured


def test_request_estimation_incluye_reference_projects_cuando_hay(monkeypatch):
    captured = _capture_post(monkeypatch)
    refs = [{"name": "Proyecto", "description": "Descripción suficientemente larga."}]
    request_estimation("una descripción válida y larga", "web_saas", "medium", "narrative", reference_projects=refs)
    assert captured["json"]["reference_projects"] == refs


def test_request_estimation_omite_reference_projects_cuando_vacio(monkeypatch):
    captured = _capture_post(monkeypatch)
    request_estimation("una descripción válida y larga", "web_saas", "medium", "narrative", reference_projects=[])
    assert "reference_projects" not in captured["json"]

    request_estimation("una descripción válida y larga", "web_saas", "medium", "narrative")
    assert "reference_projects" not in captured["json"]


# --- create_session / request_session_estimate -------------------------------


def _patch_post(monkeypatch, return_json: dict) -> dict:
    """Sustituye httpx.post (firma flexible) y recoge url + kwargs de la llamada."""
    captured: dict = {}

    class _Resp:
        is_error = False

        def json(self) -> dict:
            return return_json

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return _Resp()

    monkeypatch.setattr(api_client.httpx, "post", fake_post)
    return captured


def test_create_session_devuelve_session_id(monkeypatch):
    captured = _patch_post(monkeypatch, {"session_id": "abc-123"})
    assert api_client.create_session() == "abc-123"
    assert captured["url"].endswith("/api/v1/sessions")


def test_request_session_estimate_sin_adjuntos_no_envia_files(monkeypatch):
    captured = _patch_post(monkeypatch, {"text": "ok"})
    api_client.request_session_estimate("sid-1", "transcripción")
    assert captured["url"].endswith("/api/v1/sessions/sid-1/estimate")
    assert captured["data"] == {"transcript": "transcripción"}
    assert captured["files"] is None  # sin adjuntos: formulario simple, no multipart


def test_request_session_estimate_construye_multipart_de_adjuntos(monkeypatch):
    captured = _patch_post(monkeypatch, {"text": "ok"})
    api_client.request_session_estimate(
        "sid-1",
        "t",
        [("a.pdf", b"%PDF", "application/pdf"), ("b.docx", b"PK", None)],
    )
    # El content_type ausente cae a un valor por defecto seguro.
    assert captured["files"] == [
        ("attachments", ("a.pdf", b"%PDF", "application/pdf")),
        ("attachments", ("b.docx", b"PK", "application/octet-stream")),
    ]
