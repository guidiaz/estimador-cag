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


# Mínimos del contrato `ReferenceProject` (app/schemas.py). Se replican aquí para
# avisar en la UI antes de llegar a la API: un fallo de validación anidado vuelve
# como lista 422 y se renderiza como JSON poco legible (igual que evitamos para
# `description` validando su longitud mínima en la propia UI).
_REF_NAME_MIN = 3
_REF_DESCRIPTION_MIN = 10


def _as_optional_int(value) -> int | None:
    """Convierte el valor de horas del editor a `int | None`.

    El `NumberColumn` puede devolver `float` (p. ej. `670.0`), `None` para una
    celda vacía o `NaN` según el backend de datos. Todo lo no convertible se trata
    como «sin dato» (None) en lugar de reventar.
    """
    if value is None:
        return None
    try:
        if value != value:  # NaN: único valor distinto de sí mismo
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def build_reference_projects(rows) -> tuple[list[dict], list[str]]:
    """Transforma las filas del editor en payload `reference_projects`.

    Devuelve `(refs, problems)`:

    - `refs`: lista lista para enviar, solo con filas **completas y válidas**
      (nombre y descripción presentes y por encima de su mínimo); los campos
      opcionales en blanco se omiten para que coincidan con «sin dato».
    - `problems`: descripciones en español de filas con datos parciales o que no
      cumplen los mínimos del contrato, para avisar en la UI.

    Las filas totalmente vacías se ignoran en silencio (el editor siempre muestra
    al menos una). Función pura: sin Streamlit ni red, testeable de forma aislada.
    """
    refs: list[dict] = []
    problems: list[str] = []

    for index, row in enumerate(rows, start=1):
        name = (row.get("name") or "").strip()
        description = (row.get("description") or "").strip()
        total_hours = _as_optional_int(row.get("total_hours"))
        duration = (row.get("duration") or "").strip()
        notes = (row.get("notes") or "").strip()

        # Fila sin ningún dato: el usuario no la rellenó, se ignora.
        if not any([name, description, total_hours, duration, notes]):
            continue

        errs: list[str] = []
        if len(name) < _REF_NAME_MIN:
            errs.append(f"el nombre debe tener al menos {_REF_NAME_MIN} caracteres")
        if len(description) < _REF_DESCRIPTION_MIN:
            errs.append(
                "la descripción debe tener al menos "
                f"{_REF_DESCRIPTION_MIN} caracteres"
            )
        if errs:
            problems.append(f"Proyecto de referencia {index}: {'; '.join(errs)}.")
            continue

        ref: dict = {"name": name, "description": description}
        if total_hours is not None:
            ref["total_hours"] = total_hours
        if duration:
            ref["duration"] = duration
        if notes:
            ref["notes"] = notes
        refs.append(ref)

    return refs, problems


def request_estimation(
    description: str,
    project_type: str,
    detail_level: str,
    output_format: str,
    reference_projects: list[dict] | None = None,
) -> dict:
    """Llama a `POST /api/v1/estimate` (bloqueante) con el contrato estructurado.

    Envía un `EstimationRequest` JSON y devuelve la respuesta como dict
    (`{"text": ..., "prompt_version": ...}`). `reference_projects` (opcional) se
    incluye solo cuando trae elementos, de modo que «sin referencias» no manda el
    campo. Lanza `EstimationError` si la API responde con un error (400 validación
    de negocio, 422 validación del contrato, 502 fallo del proveedor).
    """
    payload = {
        "description": description,
        "project_type": project_type,
        "detail_level": detail_level,
        "output_format": output_format,
    }
    if reference_projects:
        payload["reference_projects"] = reference_projects
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
