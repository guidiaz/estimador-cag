import time

from fastapi import APIRouter, HTTPException

from app.schemas.estimation import EstimateRequest, EstimateResponse
from app.services.llm_service import generate_estimation

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
