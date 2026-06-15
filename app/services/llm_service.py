import hashlib
import itertools
from collections.abc import Iterator
from dataclasses import dataclass

import litellm
from litellm import Router

from app.config import settings
from app.context.examples import ESTIMATION_EXAMPLES
from app.services import cache


@dataclass
class EstimationResult:
    estimation: str
    model: str
    provider: str
    used_tokens: int

SYSTEM_PROMPT_TEMPLATE = """\
Eres un estimador de software experto. Tu tarea es generar estimaciones detalladas de \
proyectos de desarrollo de software basándote en la transcripción de reuniones con clientes.

Utiliza los ejemplos de estimaciones previas que se incluyen a continuación como referencia \
para el formato, nivel de detalle y criterios de estimación. Adapta tu respuesta al contexto \
de la nueva reunión, desglosando tareas, horas, equipo recomendado y duración estimada.

## Ejemplos de estimaciones previas

{examples}
"""


def _format_examples() -> str:
    sections: list[str] = []

    for index, example in enumerate(ESTIMATION_EXAMPLES, start=1):
        summary = example.get("meeting_summary", "").strip()
        estimation = example.get("estimation", "").strip()
        if not summary or not estimation:
            continue

        sections.append(
            f"### Ejemplo {index}\n\n"
            f"**Resumen de la reunión:**\n{summary}\n\n"
            f"**Estimación generada:**\n{estimation}"
        )

    return "\n\n---\n\n".join(sections)


def build_system_prompt() -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(examples=_format_examples())


def _system_prompt_hash(system_prompt: str) -> str:
    return hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()


def _chunk(text: str, size: int = 120) -> Iterator[str]:
    """Trocea el texto para replicar la sensación de escritura al servir un hit."""
    for start in range(0, len(text), size):
        yield text[start : start + size]


# --- LiteLLM Router: anthropic primario, openai fallback ---
#
# Política: el grupo primario ("estimador" → anthropic) se reintenta una vez ante
# errores transitorios (incluida la conexión) con `num_retries=1`; al agotarse, el
# Router cae al grupo de fallback ("estimador-fallback" → openai). En LiteLLM el
# orden es retries-dentro-de-fallbacks, justo lo requerido.

_PRIMARY = "estimador"
_FALLBACK = "estimador-fallback"

# Errores de conexión/transitorios ante los que forzamos el fallback manual en el
# path de streaming (donde el fallback interno de litellm puede no dispararse).
_CONNECTION_ERRORS = (
    litellm.APIConnectionError,
    litellm.Timeout,
    litellm.ServiceUnavailableError,
    litellm.InternalServerError,
)

_router: Router | None = None


def _provider_from_model(model: str | None) -> str:
    return "anthropic" if "claude" in (model or "").lower() else "openai"


def _api_key_for(model: str) -> str:
    """La API key sigue al proveedor del modelo, no al rol primario/fallback,
    para que intercambiar `primary_model`/`fallback_model` no cruce las claves."""
    if _provider_from_model(model) == "anthropic":
        return settings.anthropic_api_key
    return settings.openai_api_key


def _get_router() -> Router:
    global _router
    if _router is None:
        _router = Router(
            model_list=[
                {
                    "model_name": _PRIMARY,
                    "litellm_params": {
                        "model": settings.primary_model,
                        "api_key": _api_key_for(settings.primary_model),
                    },
                },
                {
                    "model_name": _FALLBACK,
                    "litellm_params": {
                        "model": settings.fallback_model,
                        "api_key": _api_key_for(settings.fallback_model),
                    },
                },
            ],
            fallbacks=[{_PRIMARY: [_FALLBACK]}],
            num_retries=1,
        )
    return _router


def _complete(
    system_prompt: str, transcription: str, max_tokens: int = 4096
) -> EstimationResult:
    # anthropic vía litellm requiere `max_tokens`; lo fijamos aquí (este path no lo
    # recibe del cliente).
    response = _get_router().completion(
        model=_PRIMARY,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": transcription},
        ],
        max_tokens=max_tokens,
    )
    usage = getattr(response, "usage", None)
    used_tokens = getattr(usage, "total_tokens", 0) or 0
    model = response.model or settings.primary_model
    hidden = getattr(response, "_hidden_params", None) or {}
    provider = hidden.get("custom_llm_provider") or _provider_from_model(model)
    return EstimationResult(
        estimation=response.choices[0].message.content or "",
        model=model,
        provider=provider,
        used_tokens=used_tokens,
    )


def _best_effort_tokens(captured: dict, messages: list, text: str) -> None:
    """Estima tokens si el proveedor no devolvió usage (anthropic+include_usage
    es históricamente inestable). Silencioso ante cualquier fallo."""
    try:
        model = captured.get("model") or settings.primary_model
        in_tok = litellm.token_counter(model=model, messages=messages)
        out_tok = litellm.token_counter(model=model, text=text)
        captured["input_tokens"] = in_tok
        captured["output_tokens"] = out_tok
        captured["used_tokens"] = in_tok + out_tok
    except Exception:  # noqa: BLE001 - el conteo es best-effort
        pass


def _stream(
    system_prompt: str, transcription: str, usage_out: dict | None, max_tokens: int
) -> Iterator[str]:
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": transcription},
    ]

    def _open(model_name: str):
        return _get_router().completion(
            model=model_name,
            messages=messages,
            max_tokens=max_tokens,
            stream=True,
            stream_options={"include_usage": True},
        )

    # Contingencia de fallback en streaming: si el primario falla por conexión
    # ANTES del primer chunk, forzamos el fallback manualmente. Es seguro porque
    # todavía no se ha emitido ningún token al cliente.
    try:
        iterator = iter(_open(_PRIMARY))
        first_chunk = next(iterator)
    except StopIteration:
        iterator = iter(())
        first_chunk = None
    except _CONNECTION_ERRORS:
        iterator = iter(_open(_FALLBACK))
        first_chunk = next(iterator, None)

    captured: dict = {}
    text_parts: list[str] = []

    def _absorb(chunk) -> str | None:
        usage = getattr(chunk, "usage", None)
        if usage:
            captured["input_tokens"] = getattr(usage, "prompt_tokens", 0) or 0
            captured["output_tokens"] = getattr(usage, "completion_tokens", 0) or 0
            captured["used_tokens"] = getattr(usage, "total_tokens", 0) or 0
        model = getattr(chunk, "model", None)
        if model:
            captured["model"] = model
        choices = getattr(chunk, "choices", None)
        return choices[0].delta.content if choices else None

    chunks = (
        itertools.chain([first_chunk], iterator)
        if first_chunk is not None
        else iterator
    )
    for chunk in chunks:
        delta = _absorb(chunk)
        if delta:
            text_parts.append(delta)
            yield delta

    if usage_out is not None:
        if not captured.get("used_tokens"):
            _best_effort_tokens(captured, messages, "".join(text_parts))
        model = captured.get("model") or settings.primary_model
        usage_out["input_tokens"] = captured.get("input_tokens", 0)
        usage_out["output_tokens"] = captured.get("output_tokens", 0)
        usage_out["used_tokens"] = captured.get("used_tokens", 0)
        usage_out["model"] = model
        usage_out["provider"] = _provider_from_model(model)


def stream_estimation(
    transcription: str, usage_out: dict | None = None, max_tokens: int = 4096
) -> Iterator[str]:
    """
    Igual que `generate_estimation` pero devuelve la estimación token a token.

    Usa el mismo system prompt (CAG) y dispatch de proveedor. `max_tokens` fija
    el límite de tokens de salida. Si se pasa `usage_out`, se rellena con
    `provider`, `model` y los tokens de entrada/salida una vez terminado el stream.

    Cacheado en Redis (namespace `estimate-stream:v2`). En un hit se reproduce el
    texto cacheado troceado y se rellena `usage_out` con las métricas guardadas
    más `cached=True`.
    """
    system_prompt = build_system_prompt()

    key = cache.build_key(
        "estimate-stream:v2",
        {
            "primary_model": settings.primary_model,
            "sp_hash": _system_prompt_hash(system_prompt),
            "transcription": transcription,
            "max_tokens": max_tokens,
        },
    )
    cached = cache.get_json(key)
    if cached is not None:
        if usage_out is not None:
            usage_out.update(cached["usage"])
            usage_out["cached"] = True
        yield from _chunk(cached["text"])
        return

    # `effective_usage` siempre existe para poder acumular y cachear las métricas,
    # aunque el llamante no haya pasado `usage_out`.
    effective_usage = usage_out if usage_out is not None else {}
    inner = _stream(system_prompt, transcription, effective_usage, max_tokens)

    buffer: list[str] = []
    for delta in inner:
        buffer.append(delta)
        yield delta

    # Cachear SOLO tras agotar el generador limpiamente (nunca en finally): si el
    # proveedor falla a mitad o el cliente se desconecta (GeneratorExit), el bucle
    # se desenrolla y no se guarda una estimación truncada. El guard de
    # `used_tokens` evita cachear un stream que terminó sin métricas.
    if effective_usage.get("used_tokens"):
        cache.set_json(
            key,
            {"text": "".join(buffer), "usage": effective_usage},
            settings.cache_ttl_seconds,
        )


def generate_estimation(transcription: str) -> EstimationResult:
    """
    Genera una estimación de software a partir de la transcripción de una reunión.

    Estructura de mensajes:
      [system]    → Instrucciones + ejemplos de estimaciones previas
      [user]      → Transcripción de la reunión a estimar
      [assistant] → Estimación generada por el modelo

    Cacheado en Redis (namespace `estimate:v2`): una transcripción repetida con el
    mismo modelo primario/system prompt se sirve desde cache sin llamar al LLM.
    """
    system_prompt = build_system_prompt()

    key = cache.build_key(
        "estimate:v2",
        {
            "primary_model": settings.primary_model,
            "sp_hash": _system_prompt_hash(system_prompt),
            "transcription": transcription,
        },
    )
    cached = cache.get_json(key)
    if cached is not None:
        return EstimationResult(
            estimation=cached["text"],
            model=cached["model"],
            provider=cached["provider"],
            used_tokens=cached["used_tokens"],
        )

    result = _complete(system_prompt, transcription)

    cache.set_json(
        key,
        {
            "text": result.estimation,
            "model": result.model,
            "provider": result.provider,
            "used_tokens": result.used_tokens,
        },
        settings.cache_ttl_seconds,
    )
    return result
