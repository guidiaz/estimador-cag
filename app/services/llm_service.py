from collections.abc import Iterator
from dataclasses import dataclass

from anthropic import Anthropic
from openai import OpenAI

from app.config import settings
from app.context.examples import ESTIMATION_EXAMPLES


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
    """
    system_prompt = build_system_prompt()
    provider = settings.llm_provider.lower()

    if provider == "openai":
        yield from _stream_openai(system_prompt, transcription, usage_out, max_tokens)
    elif provider == "anthropic":
        yield from _stream_anthropic(
            system_prompt, transcription, usage_out, max_tokens
        )
    else:
        raise ValueError(
            f"Proveedor LLM no soportado: '{settings.llm_provider}'. "
            "Usa 'openai' o 'anthropic'."
        )


def generate_estimation(transcription: str) -> EstimationResult:
    """
    Genera una estimación de software a partir de la transcripción de una reunión.

    Estructura de mensajes:
      [system]    → Instrucciones + ejemplos de estimaciones previas
      [user]      → Transcripción de la reunión a estimar
      [assistant] → Estimación generada por el modelo
    """
    system_prompt = build_system_prompt()
    provider = settings.llm_provider.lower()

    if provider == "openai":
        return _call_openai(system_prompt, transcription)
    if provider == "anthropic":
        return _call_anthropic(system_prompt, transcription)

    raise ValueError(
        f"Proveedor LLM no soportado: '{settings.llm_provider}'. "
        "Usa 'openai' o 'anthropic'."
    )
