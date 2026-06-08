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
