"""Cliente HTTP fino para la API del Estimador CAG.

La UI de Streamlit consume el backend a través de este módulo en lugar de
importar `app.services.*` directamente, de modo que UI y API quedan desacopladas
y pueden ejecutarse en procesos (o máquinas) distintos.

La URL base se configura con la variable de entorno `ESTIMADOR_API_URL`
(por defecto `http://127.0.0.1:8000`).
"""

import json
import os
from collections.abc import Iterator

import httpx

API_BASE = os.getenv("ESTIMADOR_API_URL", "http://127.0.0.1:8000").rstrip("/")

# Tiempo de espera: sin límite de lectura (el stream puede tardar), pero sí un
# límite razonable para establecer la conexión.
_TIMEOUT = httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)


class EstimationError(Exception):
    """Error reportado por la API durante la generación.

    `code` replica el status HTTP equivalente (400 validación, 502 proveedor).
    """

    def __init__(self, detail: str, code: int) -> None:
        super().__init__(detail)
        self.detail = detail
        self.code = code


def request_estimation(
    description: str,
    project_type: str,
    detail_level: str,
    output_format: str,
) -> dict:
    """Llama a `POST /api/v1/estimate` (bloqueante) con el contrato estructurado.

    Envía un `EstimationRequest` JSON y devuelve la respuesta como dict
    (`{"text": ..., "prompt_version": ...}`). Lanza `EstimationError` si la API
    responde con un error (400 validación de negocio, 422 validación del
    contrato, 502 fallo del proveedor).
    """
    payload = {
        "description": description,
        "project_type": project_type,
        "detail_level": detail_level,
        "output_format": output_format,
    }
    response = httpx.post(
        f"{API_BASE}/api/v1/estimate", json=payload, timeout=_TIMEOUT
    )
    if response.is_error:
        # FastAPI devuelve `detail` como str (400/502) o como lista de errores
        # de validación (422). Normalizamos ambos a un texto legible.
        try:
            detail = response.json().get("detail", response.text)
        except (json.JSONDecodeError, ValueError):
            detail = response.text
        if not isinstance(detail, str):
            detail = json.dumps(detail, ensure_ascii=False)
        raise EstimationError(detail, response.status_code)
    return response.json()


def request_estimation_stream(
    transcription: str, usage_out: dict | None = None, max_tokens: int = 4096
) -> Iterator[str]:
    """Llama a `POST /api/v1/estimate/stream` y devuelve la estimación token a token.

    Hace `yield` de cada fragmento de texto. Si se pasa `usage_out`, se rellena
    con las métricas finales (`provider`, `model`, `input_tokens`,
    `output_tokens`, `used_tokens`). Lanza `EstimationError` si la API reporta
    un error a mitad del stream.

    Conserva el mismo contrato que el antiguo `stream_estimation` del servicio,
    para que la UI cambie solo el origen de los datos.
    """
    payload = {"transcription": transcription, "max_tokens": max_tokens}
    with httpx.stream(
        "POST",
        f"{API_BASE}/api/v1/estimate/stream",
        json=payload,
        timeout=_TIMEOUT,
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line:
                continue
            event = json.loads(line)
            kind = event.get("type")
            if kind == "delta":
                yield event["text"]
            elif kind == "done":
                if usage_out is not None:
                    usage_out.update(event.get("usage", {}))
            elif kind == "error":
                raise EstimationError(
                    event.get("detail", "Error desconocido"),
                    event.get("code", 502),
                )


def get_context() -> dict:
    """Llama a `GET /api/v1/context` y devuelve provider, model, system_prompt y examples."""
    response = httpx.get(f"{API_BASE}/api/v1/context", timeout=_TIMEOUT)
    response.raise_for_status()
    return response.json()
