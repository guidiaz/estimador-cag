import json

import structlog
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.config import settings
from app.context.examples import ESTIMATION_EXAMPLES
from app.schemas import (
    EstimateRequest,
    EstimationRequest,
    EstimationResponse,
)
from app.services.llm_service import (
    PROMPT_VERSION,
    build_system_prompt,
    generate_estimation,
    stream_estimation,
)

router = APIRouter(tags=["estimations"])

logger = structlog.get_logger("app.estimations")


@router.post("/estimate", response_model=EstimationResponse)
def create_estimate(
    request: EstimationRequest,
    prompt_version: str = Query(
        default=PROMPT_VERSION,
        description=(
            "Versión de las plantillas de prompt a usar (p. ej. `v1`, `v2`). "
            "Una versión inexistente o con nombre no válido devuelve 400."
        ),
    ),
) -> EstimationResponse:
    # Normalizamos a minúsculas (los directorios de versión lo son): así `V2` y
    # `v2` renderizan, cachean y se devuelven igual en cualquier SO. Sin esto, un
    # FS case-insensitive (Windows) colaría con `V2` mientras Linux daría 400, y
    # además la clave de cache y la versión devuelta divergirían de la renderizada.
    prompt_version = prompt_version.lower()
    try:
        result = generate_estimation(request, version=prompt_version)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Error al generar la estimación: {exc}",
        ) from exc

    # Devolvemos la versión efectivamente usada (la que renderizó sin error).
    return EstimationResponse(text=result.estimation, prompt_version=prompt_version)


@router.post("/estimate/stream")
def create_estimate_stream(request: EstimateRequest) -> StreamingResponse:
    """
    Genera la estimación en streaming (NDJSON, una línea JSON por evento):

      {"type": "delta", "text": "..."}            → fragmento de texto
      {"type": "done", "usage": {...}}            → métricas finales
      {"type": "error", "code": 400|502, ...}     → fallo durante la generación

    Una vez iniciado el stream el status HTTP ya es 200, por lo que los errores
    se transmiten como un registro `error` en el cuerpo, no como código HTTP.
    """

    def ndjson():
        usage: dict = {}
        try:
            for delta in stream_estimation(
                request.transcription, usage, request.max_tokens
            ):
                yield json.dumps({"type": "delta", "text": delta}) + "\n"
        except ValueError as exc:
            logger.warning("estimate.stream.error", code=400, error=str(exc))
            yield json.dumps({"type": "error", "code": 400, "detail": str(exc)}) + "\n"
            return
        except Exception as exc:  # noqa: BLE001 - reportar cualquier fallo del proveedor
            logger.exception("estimate.stream.error", code=502, error=str(exc))
            yield json.dumps({"type": "error", "code": 502, "detail": str(exc)}) + "\n"
            return
        yield json.dumps({"type": "done", "usage": usage}) + "\n"

    return StreamingResponse(ndjson(), media_type="application/x-ndjson")


@router.get("/context")
def get_context() -> dict:
    """Contexto estático que alimenta el CAG, para mostrarlo en la UI."""
    return {
        "provider": "anthropic (fallback: openai)",
        "model": settings.primary_model,
        "fallback_model": settings.fallback_model,
        "system_prompt": build_system_prompt(),
        "examples": ESTIMATION_EXAMPLES,
    }
