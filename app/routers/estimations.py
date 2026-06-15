import json
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.config import settings
from app.context.examples import ESTIMATION_EXAMPLES
from app.schemas.estimation import EstimateRequest, EstimateResponse
from app.services.llm_service import (
    build_system_prompt,
    generate_estimation,
    stream_estimation,
)

router = APIRouter(tags=["estimations"])


@router.post("/estimate", response_model=EstimateResponse)
def create_estimate(request: EstimateRequest) -> EstimateResponse:
    try:
        result = generate_estimation(request.transcription)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Error al generar la estimación: {exc}",
        ) from exc

    return EstimateResponse(
        estimation=result.estimation,
        model=result.model,
        provider=result.provider,
        usedTokens=result.used_tokens,
        timestamp=int(time.time()),
    )


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
            yield json.dumps({"type": "error", "code": 400, "detail": str(exc)}) + "\n"
            return
        except Exception as exc:  # noqa: BLE001 - reportar cualquier fallo del proveedor
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
