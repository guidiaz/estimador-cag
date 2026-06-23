"""Tests de render de la UI Streamlit (`streamlit_app.py`) con `AppTest`.

Ejecutan el script en headless (sin navegador ni API real: `api_client` se
mockea) para cubrir lo que un `py_compile` no ve: que la página renderiza sin
excepción en la primera carga (init de `session_state`, uso correcto de los
widgets) y que el panel de memoria del proyecto muestra los hechos. No drivean el
`file_uploader` (su soporte en AppTest es limitado y la ruta de adjuntos ya está
cubierta a nivel de backend y de cliente HTTP).
"""

import api_client
from streamlit.testing.v1 import AppTest


def _app(monkeypatch) -> AppTest:
    # La carga de la página crea una sesión: la mockeamos para no tocar la API.
    monkeypatch.setattr(api_client, "create_session", lambda: "test-session-id")
    return AppTest.from_file("streamlit_app.py")


def test_la_pagina_carga_sin_excepcion(monkeypatch):
    at = _app(monkeypatch).run()

    assert not at.exception
    # Sesión creada y estado inicial sembrado.
    assert at.session_state["session_id"] == "test-session-id"
    assert at.session_state["turns"] == []
    # Hay un campo de transcripción y al menos un botón (enviar / nueva conversación).
    assert len(at.text_area) >= 1
    assert len(at.button) >= 1


def test_el_panel_de_memoria_muestra_los_hechos(monkeypatch):
    at = _app(monkeypatch).run()

    # Sembramos metadata como si un turno la hubiera devuelto y re-renderizamos.
    at.session_state["project_metadata"] = {
        "project_name": "Acme CRM",
        "assumed_team_size": 4,
        "mentioned_technologies": ["React", "FastAPI"],
        "agreed_scope": None,
    }
    at.run()

    assert not at.exception
    rendered = " ".join(md.value for md in at.markdown)
    assert "Acme CRM" in rendered
    assert "React, FastAPI" in rendered  # lista unida por comas
    # El campo vacío (agreed_scope) no debe aparecer como etiqueta.
    assert "Alcance acordado" not in rendered


def test_boton_nueva_conversacion_resetea_y_recrea_sesion(monkeypatch):
    ids = iter(["sid-1", "sid-2"])
    monkeypatch.setattr(api_client, "create_session", lambda: next(ids))

    at = AppTest.from_file("streamlit_app.py").run()
    assert at.session_state["session_id"] == "sid-1"

    at.session_state["turns"] = [{"transcript": "x", "attachments": [],
                                  "estimation": "y", "model": "m",
                                  "provider": "p", "used_tokens": 1, "elapsed": 0.1}]
    # Pulsar «Nueva conversación» (último botón, en la barra lateral).
    at.button[-1].click()
    at.run()

    assert not at.exception
    assert at.session_state["session_id"] == "sid-2"
    assert at.session_state["turns"] == []
