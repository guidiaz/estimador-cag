import json
import uuid

import structlog
from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from app.config import settings
from app.context.examples import ESTIMATION_EXAMPLES
from app.schemas import (
    AttachmentInfo,
    EstimateRequest,
    EstimationRequest,
    EstimationResponse,
    SessionEstimationResponse,
    SessionResponse,
)
from app.prompts.loader import render_session_system_prompt
from app.services import documents
from app.services.llm_service import (
    PROMPT_VERSION,
    build_system_prompt,
    extract_project_metadata,
    generate_estimation,
    generate_from_messages,
    stream_estimation,
)
from app.services.sessions import sessions

router = APIRouter(tags=["estimations"])

logger = structlog.get_logger("app.estimations")


@router.post("/sessions", response_model=SessionResponse, status_code=201)
def create_session() -> SessionResponse:
    """Crea una sesión de memoria conversacional y devuelve su `session_id`.

    El identificador es un UUID v4. El cliente lo conserva y lo reenvía en cada
    petición posterior para reutilizar la memoria (hilo + metadatos del proyecto)
    entre páginas. La sesión se materializa ya en el almacén en memoria del proceso
    (volátil por diseño en esta fase, ver `app/services/sessions.py`).
    """
    session_id = str(uuid.uuid4())
    sessions.get_or_create(session_id)
    logger.info("session.created", session_id=session_id, active_sessions=len(sessions))
    return SessionResponse(session_id=session_id)


def _build_session_user_content(
    transcript: str, extracted: list[tuple[str, str]]
) -> str:
    """Compone el mensaje de usuario: transcripción + documentación adjunta.

    Etiqueta cada adjunto con su nombre bajo una sección propia para que el modelo
    distinga la transcripción de la documentación complementaria."""
    sections = [transcript.strip()]
    if extracted:
        sections.append("## Documentación adjunta")
        for filename, text in extracted:
            sections.append(f"### {filename}\n\n{text}")
    return "\n\n".join(sections)


@router.post(
    "/sessions/{session_id}/estimate", response_model=SessionEstimationResponse
)
async def create_session_estimate(
    session_id: str,
    transcript: str = Form(
        ...,
        min_length=1,
        description="Texto de la transcripción de la reunión",
    ),
    attachments: list[UploadFile] = File(
        default=[],
        description="Documentación complementaria opcional (PDF o Word .docx)",
    ),
) -> SessionEstimationResponse:
    """Genera una estimación en el contexto de una sesión, con adjuntos opcionales.

    Acepta `multipart/form-data`: `transcript` (texto) y `attachments` (lista
    opcional de PDF/Word). De cada adjunto se extrae **texto plano en el backend**
    (ver `app/services/documents.py`) —no se usa la Files API del proveedor— y se
    añade etiquetado al mensaje de usuario. La estimación se genera viendo el hilo
    completo de la sesión (memoria multi-turno) y el nuevo turno se persiste en ella.

    Errores: 404 si la sesión no existe (el almacén es volátil; ver POST /sessions),
    400 si un adjunto no es soportado/está corrupto o se exceden los límites, 502 si
    falla el proveedor.
    """
    session = sessions.get(session_id)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail="Sesión no encontrada. Crea una con POST /sessions.",
        )

    if len(attachments) > documents.MAX_ATTACHMENTS:
        raise HTTPException(
            status_code=400,
            detail=f"Máximo {documents.MAX_ATTACHMENTS} adjuntos por petición.",
        )

    extracted: list[tuple[str, str]] = []  # (filename, texto) de los que aportan texto
    infos: list[AttachmentInfo] = []
    for file in attachments:
        filename = file.filename or "adjunto"
        # Rechazamos por tamaño ANTES de leer los bytes a memoria: así un adjunto
        # enorme no llega siquiera a cargarse en el worker (`file.size` lo aporta
        # Starlette desde el multipart).
        if file.size is not None and file.size > documents.MAX_FILE_BYTES:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"El archivo {filename!r} supera el máximo de "
                    f"{documents.MAX_FILE_BYTES // (1024 * 1024)} MB"
                ),
            )
        data = await file.read()
        try:
            # pypdf/python-docx son síncronos: a un threadpool para no bloquear el loop.
            text = await run_in_threadpool(documents.extract_text, filename, data)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        infos.append(AttachmentInfo(filename=filename, extracted_chars=len(text)))
        if text:
            extracted.append((filename, text))

    user_content = _build_session_user_content(transcript, extracted)

    # Hilo para la llamada: el system prompt se regenera ahora a partir del
    # project_metadata actual (incluye el bloque <project_metadata> con los hechos
    # conocidos, vacío en la primera llamada) y `to_messages_list` lo antepone a la
    # ventana de turnos + el nuevo user. No mutamos la sesión hasta que la
    # generación tiene éxito: así un 502 no deja un turno de usuario huérfano.
    thread = session.history.to_messages_list(
        render_session_system_prompt(session.metadata), pending_user=user_content
    )

    try:
        # litellm es síncrono: al threadpool para no bloquear el event loop durante
        # el round-trip al proveedor (segundos), que es la operación más larga aquí.
        result = await run_in_threadpool(generate_from_messages, thread)
    except Exception as exc:  # noqa: BLE001 - cualquier fallo del proveedor → 502
        raise HTTPException(
            status_code=502,
            detail=f"Error al generar la estimación: {exc}",
        ) from exc

    session.history.add("user", user_content)
    session.history.add("assistant", result.estimation)

    # Segunda llamada al LLM: extrae los hechos del proyecto de esta interacción y
    # los funde en la metadata de la sesión, para enriquecer el siguiente turno.
    # Es síncrona (al threadpool) y degrada con elegancia: un fallo aquí deja la
    # metadata sin avanzar pero no afecta a la estimación ya generada.
    session.metadata = await run_in_threadpool(
        extract_project_metadata, session.metadata, user_content, result.estimation
    )

    logger.info(
        "session.estimate.completed",
        session_id=session_id,
        attachment_count=len(attachments),
        turns=session.history.turn_count(),
        model=result.model,
        provider=result.provider,
        used_tokens=result.used_tokens,
        transcript_chars=len(transcript),
        extracted_chars=sum(i.extracted_chars for i in infos),
        known_metadata_fields=[
            name
            for name in type(session.metadata).model_fields
            if getattr(session.metadata, name) not in (None, "", [])
        ],
    )

    return SessionEstimationResponse(
        text=result.estimation,
        model=result.model,
        provider=result.provider,
        used_tokens=result.used_tokens,
        attachments=infos,
        project_metadata=session.metadata,
    )


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
