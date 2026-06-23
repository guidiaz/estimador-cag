"""Tests de integración del flujo de sesión con `httpx.AsyncClient`.

A diferencia de los tests de endpoint (que parchean las funciones del router),
estos ejercitan la pila **completa** sobre la app ASGI —enrutado, almacén de
sesiones en memoria entre peticiones, extracción real de PDF (`pypdf`), render del
prompt, ventana deslizante y la tubería real de extracción+merge de
`ProjectMetadata`— y solo estuban el **único** punto que sale del proceso: la
llamada al proveedor, `llm_service._complete_messages`. Tanto la estimación
(`generate_from_messages`) como la extracción de metadata (`extract_project_metadata`)
pasan por ahí, así que un solo stub deja correr todo lo demás de verdad.

El stub distingue las dos clases de llamada por el system prompt: el de extracción
empieza por «Eres un extractor de datos…», el de estimación no.
"""

import httpx
from httpx import ASGITransport

from app.main import app
from app.services import llm_service
from app.services.llm_service import EstimationResult
from app.services.sessions import MAX_TURNS

_EXTRACTION_MARKER = "extractor de datos"


def _result(text: str) -> EstimationResult:
    return EstimationResult(
        estimation=text, model="stub", provider="stub", used_tokens=1
    )


def _is_extraction(messages: list[dict]) -> bool:
    return _EXTRACTION_MARKER in messages[0]["content"]


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    )


def _make_pdf(text: str) -> bytes:
    """PDF mínimo y válido con una línea de texto extraíble (pypdf solo lee)."""
    stream = b"BT /F1 24 Tf 100 700 Td (" + text.encode("latin-1") + b") Tj ET"
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    xref_pos = len(out)
    out += b"xref\n0 " + str(len(objs) + 1).encode() + b"\n0000000000 65535 f \n"
    for off in offsets:
        out += ("%010d 00000 n \n" % off).encode()
    out += (
        b"trailer\n<< /Size " + str(len(objs) + 1).encode() + b" /Root 1 0 R >>\n"
        b"startxref\n" + str(xref_pos).encode() + b"\n%%EOF"
    )
    return bytes(out)


# --- 1) Dos peticiones encadenadas actualizan project_metadata ----------------


async def test_dos_peticiones_actualizan_project_metadata(monkeypatch):
    # La extracción devuelve JSON distinto por turno; la estimación, prosa.
    extraction_json = iter(
        [
            '{"project_name": "Acme CRM", "mentioned_technologies": ["React"]}',
            '{"assumed_team_size": 4, "mentioned_technologies": ["Postgres"]}',
        ]
    )

    def fake_complete(messages, max_tokens=4096):
        if _is_extraction(messages):
            return _result(next(extraction_json))
        return _result("## Estimación\n\n40 horas")

    monkeypatch.setattr(llm_service, "_complete_messages", fake_complete)

    async with _client() as client:
        session_id = (await client.post("/api/v1/sessions")).json()["session_id"]

        r1 = await client.post(
            f"/api/v1/sessions/{session_id}/estimate",
            data={"transcript": "Reunión 1: el proyecto Acme se hará en React."},
        )
        assert r1.status_code == 200
        meta1 = r1.json()["project_metadata"]
        assert meta1["project_name"] == "Acme CRM"
        assert meta1["mentioned_technologies"] == ["React"]
        assert meta1["assumed_team_size"] is None

        r2 = await client.post(
            f"/api/v1/sessions/{session_id}/estimate",
            data={"transcript": "Reunión 2: equipo de 4 personas, añadimos Postgres."},
        )
        assert r2.status_code == 200
        meta2 = r2.json()["project_metadata"]
        # Merge real: conserva lo del turno 1, añade el tamaño de equipo y UNE
        # las tecnologías (no las sobrescribe).
        assert meta2["project_name"] == "Acme CRM"
        assert meta2["assumed_team_size"] == 4
        assert meta2["mentioned_technologies"] == ["React", "Postgres"]


# --- 2) Un PDF adjunto influye en la estimación -------------------------------


async def test_el_pdf_adjunto_influye_en_la_estimacion(monkeypatch):
    # Marcador presente solo en el PDF: si llega al prompt, la estimación cambia.
    secret = "RESTRICCION-PRESUPUESTO-MAXIMO-50000-EUROS"

    def fake_complete(messages, max_tokens=4096):
        if _is_extraction(messages):
            return _result("{}")
        user = messages[-1]["content"]
        marca = "con-restriccion" if secret in user else "sin-restriccion"
        return _result(f"## Estimación\n\nCondicionantes: {marca}")

    monkeypatch.setattr(llm_service, "_complete_messages", fake_complete)

    transcript = "Estima el coste del proyecto descrito en la reunión."
    async with _client() as client:
        # Sesión A: misma transcripción, SIN adjunto.
        sid_a = (await client.post("/api/v1/sessions")).json()["session_id"]
        sin = await client.post(
            f"/api/v1/sessions/{sid_a}/estimate", data={"transcript": transcript}
        )
        # Sesión B: misma transcripción, CON el PDF (única diferencia).
        sid_b = (await client.post("/api/v1/sessions")).json()["session_id"]
        con = await client.post(
            f"/api/v1/sessions/{sid_b}/estimate",
            data={"transcript": transcript},
            files=[("attachments", ("presupuesto.pdf", _make_pdf(secret), "application/pdf"))],
        )

    assert sin.status_code == 200 and con.status_code == 200
    # El contenido del PDF (extraído de verdad por pypdf) llegó al modelo y cambió
    # el campo «Condicionantes» del output. La única variable fue el adjunto.
    assert "sin-restriccion" in sin.json()["text"]
    assert "con-restriccion" in con.json()["text"]
    assert sin.json()["text"] != con.json()["text"]
    # Acuse: el adjunto se procesó y aportó texto.
    assert con.json()["attachments"][0]["filename"] == "presupuesto.pdf"
    assert con.json()["attachments"][0]["extracted_chars"] > 0


# --- 3) 8 turnos: el historial enviado al LLM no supera MAX_TURNS -------------


async def test_ocho_turnos_no_superan_max_turns_en_el_historial(monkeypatch):
    sent_threads: list[list[dict]] = []

    def fake_complete(messages, max_tokens=4096):
        if _is_extraction(messages):
            return _result("{}")  # no aporta hechos: metadata intacta
        sent_threads.append(messages)
        return _result("ok")

    monkeypatch.setattr(llm_service, "_complete_messages", fake_complete)

    async with _client() as client:
        session_id = (await client.post("/api/v1/sessions")).json()["session_id"]
        for i in range(8):
            r = await client.post(
                f"/api/v1/sessions/{session_id}/estimate",
                data={"transcript": f"Turno {i}: hecho número {i}."},
            )
            assert r.status_code == 200

    assert len(sent_threads) == 8
    for thread in sent_threads:
        # Historial efectivo = turnos COMPLETOS arrastrados (cada uno con su
        # `assistant`); el `user` en curso es la petición, no historial. Nunca
        # supera MAX_TURNS, por mucho que crezca la conversación.
        completed_turns = sum(1 for m in thread if m["role"] == "assistant")
        assert completed_turns <= MAX_TURNS
        # Y el total de turnos del hilo (incluido el actual) a lo sumo MAX_TURNS + 1.
        users = sum(1 for m in thread if m["role"] == "user")
        assert users <= MAX_TURNS + 1
        # Siempre exactamente un system al frente (invariante, regenerado).
        assert thread[0]["role"] == "system"

    # Para el 8º turno el tope ya está saturado: justo MAX_TURNS turnos completos.
    assert sum(1 for m in sent_threads[-1] if m["role"] == "assistant") == MAX_TURNS
