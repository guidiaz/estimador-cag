"""Tests del endpoint `POST /api/v1/sessions`.

Cubren el contrato de creación de sesión: status 201, cuerpo snake_case con un
`session_id` que es un UUID v4, identificadores distintos por llamada y que la
sesión queda materializada en el almacén en memoria del proceso. No tocan el
modelo: el endpoint solo acuña el id y registra la sesión.
"""

import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.services.sessions import sessions

client = TestClient(app)


def test_crea_sesion_devuelve_201_y_uuid4():
    resp = client.post("/api/v1/sessions")

    assert resp.status_code == 201
    body = resp.json()
    assert list(body.keys()) == ["session_id"]  # snake_case, sin campos extra
    parsed = uuid.UUID(body["session_id"])
    assert parsed.version == 4


def test_cada_llamada_genera_un_id_distinto():
    a = client.post("/api/v1/sessions").json()["session_id"]
    b = client.post("/api/v1/sessions").json()["session_id"]

    assert a != b


def test_la_sesion_queda_registrada_en_el_almacen():
    session_id = client.post("/api/v1/sessions").json()["session_id"]

    assert sessions.get(session_id) is not None
