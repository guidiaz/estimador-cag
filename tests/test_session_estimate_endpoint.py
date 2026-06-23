"""Tests del endpoint `POST /api/v1/sessions/{id}/estimate`.

Cubren el contrato multipart (transcript + adjuntos) y el cableado con la sesión,
con el LLM monkeypatcheado (no se llama al proveedor). Se verifica: 404 si la
sesión no existe; happy path con adjunto Word (texto extraído e incorporado al
hilo, turno persistido en la memoria, acuse `attachments`); errores de adjunto
(tipo no soportado → 400, exceso de adjuntos → 400); y que un 502 del proveedor no
deja un turno huérfano en la memoria de la sesión.
"""

import io

import pytest
from docx import Document
from fastapi.testclient import TestClient

from app.main import app
from app.routers import estimations
from app.services.llm_service import EstimationResult

client = TestClient(app)


def _make_docx(text: str) -> bytes:
    doc = Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _new_session() -> str:
    return client.post("/api/v1/sessions").json()["session_id"]


@pytest.fixture
def fake_llm(monkeypatch):
    """Sustituye las DOS llamadas al LLM del endpoint (generación y extracción de
    metadata) y captura el hilo que recibe la generación."""
    calls: list[list[dict]] = []

    def _fake(messages, max_tokens=4096):
        calls.append(messages)
        return EstimationResult(
            estimation="## Estimación\n\n40h",
            model="anthropic/claude-haiku-4-5",
            provider="anthropic",
            used_tokens=123,
        )

    # La extracción no debe llamar al proveedor en los tests: por defecto deja la
    # metadata como está (los tests que la ejercitan la sobreescriben).
    monkeypatch.setattr(estimations, "generate_from_messages", _fake)
    monkeypatch.setattr(
        estimations,
        "extract_project_metadata",
        lambda current, user_text, assistant_text: current,
    )
    return calls


# --- 404 -----------------------------------------------------------------------


def test_sesion_inexistente_devuelve_404(fake_llm):
    resp = client.post(
        "/api/v1/sessions/no-existe/estimate",
        data={"transcript": "Reunión inicial sobre la app de reservas."},
    )
    assert resp.status_code == 404
    assert fake_llm == []  # no se llega a llamar al modelo


# --- happy path ----------------------------------------------------------------


def test_estimacion_con_adjunto_docx(fake_llm):
    session_id = _new_session()
    docx = _make_docx("Requisito: panel de KPIs con SSO.")

    resp = client.post(
        f"/api/v1/sessions/{session_id}/estimate",
        data={"transcript": "Resumen de la reunión con el cliente."},
        files=[("attachments", ("reqs.docx", docx, "application/octet-stream"))],
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] == "## Estimación\n\n40h"
    assert body["provider"] == "anthropic"
    assert body["used_tokens"] == 123
    assert body["attachments"] == [
        {"filename": "reqs.docx", "extracted_chars": len("Requisito: panel de KPIs con SSO.")}
    ]
    # La respuesta incluye la memoria estructurada del proyecto (vacía aquí: el
    # `fake_llm` deja la extracción como identidad).
    assert body["project_metadata"] == {
        "project_name": None,
        "assumed_team_size": None,
        "mentioned_technologies": [],
        "agreed_scope": None,
    }

    # El hilo enviado al modelo incluye el system (CAG) y el texto extraído del adjunto.
    thread = fake_llm[0]
    assert thread[0]["role"] == "system"
    user_msg = thread[-1]["content"]
    assert "Resumen de la reunión con el cliente." in user_msg
    assert "Documentación adjunta" in user_msg
    assert "Requisito: panel de KPIs con SSO." in user_msg


def test_turno_se_persiste_en_la_memoria_de_la_sesion(fake_llm):
    from app.services.sessions import sessions

    session_id = _new_session()
    client.post(
        f"/api/v1/sessions/{session_id}/estimate",
        data={"transcript": "Primera reunión."},
    )

    history = sessions.get(session_id).history
    assert history.turn_count() == 1  # un intercambio user+assistant
    # El system se regenera al construir la lista; la ventana guarda user+assistant.
    roles = [m["role"] for m in history.to_messages_list("SYS")]
    assert roles == ["system", "user", "assistant"]

    # Un segundo turno se acumula sobre el primero (memoria multi-turno).
    client.post(
        f"/api/v1/sessions/{session_id}/estimate",
        data={"transcript": "Segunda reunión, nuevos requisitos."},
    )
    history = sessions.get(session_id).history
    assert history.turn_count() == 2


def test_metadata_extraida_se_inyecta_en_el_siguiente_turno(monkeypatch):
    from app.services.sessions import ProjectMetadata

    threads: list[list[dict]] = []

    def _gen(messages, max_tokens=4096):
        threads.append(messages)
        return EstimationResult(
            estimation="estimación", model="m", provider="anthropic", used_tokens=1
        )

    # El primer turno «descubre» el nombre del proyecto; el segundo debe verlo ya.
    def _extract(current, user_text, assistant_text):
        return current.merged_with(ProjectMetadata(project_name="Acme CRM"))

    monkeypatch.setattr(estimations, "generate_from_messages", _gen)
    monkeypatch.setattr(estimations, "extract_project_metadata", _extract)

    session_id = _new_session()
    url = f"/api/v1/sessions/{session_id}/estimate"
    client.post(url, data={"transcript": "Primera reunión."})
    client.post(url, data={"transcript": "Segunda reunión."})

    first_system = threads[0][0]["content"]
    second_system = threads[1][0]["content"]
    # Turno 1: bloque vacío; turno 2: el hecho extraído ya está en el system prompt.
    assert "project_name: Acme CRM" not in first_system
    assert "project_name: Acme CRM" in second_system


def test_la_respuesta_refleja_la_metadata_extraida(monkeypatch):
    from app.services.sessions import ProjectMetadata

    def _gen(messages, max_tokens=4096):
        return EstimationResult(
            estimation="est", model="m", provider="anthropic", used_tokens=1
        )

    def _extract(current, user_text, assistant_text):
        return current.merged_with(
            ProjectMetadata(project_name="Acme CRM", mentioned_technologies=["React"])
        )

    monkeypatch.setattr(estimations, "generate_from_messages", _gen)
    monkeypatch.setattr(estimations, "extract_project_metadata", _extract)

    session_id = _new_session()
    resp = client.post(
        f"/api/v1/sessions/{session_id}/estimate",
        data={"transcript": "Reunión con Acme sobre su CRM en React."},
    )

    meta = resp.json()["project_metadata"]
    assert meta["project_name"] == "Acme CRM"
    assert meta["mentioned_technologies"] == ["React"]


def test_sin_adjuntos_funciona(fake_llm):
    session_id = _new_session()
    resp = client.post(
        f"/api/v1/sessions/{session_id}/estimate",
        data={"transcript": "Reunión sin documentación adjunta."},
    )
    assert resp.status_code == 200
    assert resp.json()["attachments"] == []


# --- errores de adjunto --------------------------------------------------------


def test_adjunto_no_soportado_devuelve_400(fake_llm):
    session_id = _new_session()
    resp = client.post(
        f"/api/v1/sessions/{session_id}/estimate",
        data={"transcript": "Reunión con un adjunto inválido."},
        files=[("attachments", ("notas.txt", b"texto plano", "text/plain"))],
    )
    assert resp.status_code == 400
    assert "no soportado" in resp.json()["detail"]


def test_demasiados_adjuntos_devuelve_400(fake_llm):
    from app.services.documents import MAX_ATTACHMENTS

    session_id = _new_session()
    docx = _make_docx("contenido")
    files = [
        ("attachments", (f"d{i}.docx", docx, "application/octet-stream"))
        for i in range(MAX_ATTACHMENTS + 1)
    ]
    resp = client.post(
        f"/api/v1/sessions/{session_id}/estimate",
        data={"transcript": "Demasiados adjuntos."},
        files=files,
    )
    assert resp.status_code == 400
    assert "Máximo" in resp.json()["detail"]


def test_transcript_vacio_devuelve_422(fake_llm):
    session_id = _new_session()
    resp = client.post(
        f"/api/v1/sessions/{session_id}/estimate",
        data={"transcript": ""},
    )
    assert resp.status_code == 422


# --- fallo del proveedor -------------------------------------------------------


def test_error_del_proveedor_no_deja_turno_huerfano(monkeypatch):
    from app.services.sessions import sessions

    def _boom(messages, max_tokens=4096):
        raise RuntimeError("proveedor caído")

    monkeypatch.setattr(estimations, "generate_from_messages", _boom)

    session_id = _new_session()
    resp = client.post(
        f"/api/v1/sessions/{session_id}/estimate",
        data={"transcript": "Reunión que fallará."},
    )
    assert resp.status_code == 502

    # La memoria no debe contener un user sin respuesta: el turno no se persistió.
    history = sessions.get(session_id).history
    assert history.turn_count() == 0
