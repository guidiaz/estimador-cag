import hashlib
from collections.abc import Iterator
from dataclasses import dataclass

from anthropic import Anthropic
from openai import OpenAI

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


def _call_openai(system_prompt: str, transcription: str) -> EstimationResult:
    client = OpenAI(api_key=settings.openai_api_key)
    response = client.chat.completions.create(
        model=settings.resolved_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": transcription},
        ],
    )
    used_tokens = response.usage.total_tokens if response.usage else 0
    return EstimationResult(
        estimation=response.choices[0].message.content or "",
        model=settings.resolved_model,
        provider="openai",
        used_tokens=used_tokens,
    )


def _call_anthropic(system_prompt: str, transcription: str) -> EstimationResult:
    client = Anthropic(api_key=settings.anthropic_api_key)
    response = client.messages.create(
        model=settings.resolved_model,
        max_tokens=4096,
        system=system_prompt,
        messages=[
            {"role": "user", "content": transcription},
        ],
    )
    used_tokens = response.usage.input_tokens + response.usage.output_tokens
    return EstimationResult(
        estimation=response.content[0].text,
        model=settings.resolved_model,
        provider="anthropic",
        used_tokens=used_tokens,
    )


def _stream_openai(
    system_prompt: str, transcription: str, usage_out: dict | None, max_tokens: int
) -> Iterator[str]:
    client = OpenAI(api_key=settings.openai_api_key)
    stream = client.chat.completions.create(
        model=settings.resolved_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": transcription},
        ],
        max_tokens=max_tokens,
        stream=True,
        stream_options={"include_usage": True},
    )
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content
        if chunk.usage and usage_out is not None:
            usage_out["input_tokens"] = chunk.usage.prompt_tokens
            usage_out["output_tokens"] = chunk.usage.completion_tokens
            usage_out["used_tokens"] = chunk.usage.total_tokens

    if usage_out is not None:
        usage_out.setdefault("input_tokens", 0)
        usage_out.setdefault("output_tokens", 0)
        usage_out.setdefault("used_tokens", 0)
        usage_out["model"] = settings.resolved_model
        usage_out["provider"] = "openai"


def _stream_anthropic(
    system_prompt: str, transcription: str, usage_out: dict | None, max_tokens: int
) -> Iterator[str]:
    client = Anthropic(api_key=settings.anthropic_api_key)
    with client.messages.stream(
        model=settings.resolved_model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[
            {"role": "user", "content": transcription},
        ],
    ) as stream:
        yield from stream.text_stream
        final_message = stream.get_final_message()

    if usage_out is not None:
        usage_out["input_tokens"] = final_message.usage.input_tokens
        usage_out["output_tokens"] = final_message.usage.output_tokens
        usage_out["used_tokens"] = (
            final_message.usage.input_tokens + final_message.usage.output_tokens
        )
        usage_out["model"] = settings.resolved_model
        usage_out["provider"] = "anthropic"


def stream_estimation(
    transcription: str, usage_out: dict | None = None, max_tokens: int = 4096
) -> Iterator[str]:
    """
    Igual que `generate_estimation` pero devuelve la estimación token a token.

    Usa el mismo system prompt (CAG) y dispatch de proveedor. `max_tokens` fija
    el límite de tokens de salida. Si se pasa `usage_out`, se rellena con
    `provider`, `model` y los tokens de entrada/salida una vez terminado el stream.

    Cacheado en Redis (namespace `estimate-stream:v1`). En un hit se reproduce el
    texto cacheado troceado y se rellena `usage_out` con las métricas guardadas
    más `cached=True`.
    """
    system_prompt = build_system_prompt()
    provider = settings.llm_provider.lower()
    model = settings.resolved_model

    key = cache.build_key(
        "estimate-stream:v1",
        {
            "provider": provider,
            "model": model,
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

    if provider == "openai":
        inner = _stream_openai(system_prompt, transcription, effective_usage, max_tokens)
    elif provider == "anthropic":
        inner = _stream_anthropic(
            system_prompt, transcription, effective_usage, max_tokens
        )
    else:
        raise ValueError(
            f"Proveedor LLM no soportado: '{settings.llm_provider}'. "
            "Usa 'openai' o 'anthropic'."
        )

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

    Cacheado en Redis (namespace `estimate:v1`): una transcripción repetida con el
    mismo proveedor/modelo/system prompt se sirve desde cache sin llamar al LLM.
    """
    system_prompt = build_system_prompt()
    provider = settings.llm_provider.lower()
    model = settings.resolved_model

    key = cache.build_key(
        "estimate:v1",
        {
            "provider": provider,
            "model": model,
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

    if provider == "openai":
        result = _call_openai(system_prompt, transcription)
    elif provider == "anthropic":
        result = _call_anthropic(system_prompt, transcription)
    else:
        raise ValueError(
            f"Proveedor LLM no soportado: '{settings.llm_provider}'. "
            "Usa 'openai' o 'anthropic'."
        )

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
