"""Contrato (Pydantic v2) entre el cliente y el servicio de estimación.

Dos contratos conviven:

- **`EstimationRequest` / `EstimationResponse`**: el contrato estructurado del
  endpoint bloqueante `POST /api/v1/estimate`. El cliente describe el proyecto
  con campos tipados (tipo de proyecto, nivel de detalle, formato) y recibe la
  estimación como texto libre más la versión del prompt que la generó.
- **`EstimateRequest` / `EstimateResponse`** (heredados): siguen sirviendo al
  endpoint de streaming `POST /api/v1/estimate/stream`, que aún opera sobre una
  transcripción libre. Se conservan para no romper ese flujo.
"""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ProjectType(str, Enum):
    MOBILE_APP = "mobile_app"
    WEB_SAAS = "web_saas"
    INTERNAL_TOOL = "internal_tool"
    DATA_PIPELINE = "data_pipeline"


class DetailLevel(str, Enum):
    SUMMARY = "summary"
    MEDIUM = "medium"
    DETAILED = "detailed"


class OutputFormat(str, Enum):
    PHASES_TABLE = "phases_table"
    LINE_ITEMS = "line_items"
    NARRATIVE = "narrative"


class ReferenceProject(BaseModel):
    """Proyecto similar ya entregado, aportado por el cliente como contexto.

    Sirve de ancla de calibración: permite estimar contra trabajo real (horas y
    duración efectivas) y no solo contra los ejemplos few-shot inventados del
    prompt. Todos los campos salvo `name`/`description` son opcionales: el cliente
    aporta lo que sepa."""

    name: str = Field(
        min_length=3,
        max_length=120,
        description="Nombre o título corto del proyecto de referencia",
    )
    description: str = Field(
        min_length=10,
        max_length=1000,
        description="Alcance del proyecto: qué incluía y qué se entregó",
    )
    total_hours: int | None = Field(
        default=None,
        ge=1,
        le=100_000,
        description="Horas reales que costó, si se conocen",
    )
    duration: str | None = Field(
        default=None,
        max_length=60,
        description="Duración real (texto libre, p. ej. «8 semanas»)",
    )
    notes: str | None = Field(
        default=None,
        max_length=500,
        description="Aprendizajes, sorpresas o riesgos observados",
    )


class EstimationRequest(BaseModel):
    """Petición estructurada del endpoint bloqueante `POST /estimate`."""

    description: str = Field(
        min_length=20,
        max_length=2000,
        description="Descripción del proyecto o resumen de la reunión a estimar",
    )
    project_type: ProjectType = Field(description="Tipo de proyecto a estimar")
    detail_level: DetailLevel = Field(description="Granularidad de la estimación")
    output_format: OutputFormat = Field(description="Formato del texto resultante")
    reference_projects: list[ReferenceProject] | None = Field(
        default=None,
        max_length=8,
        description=(
            "Proyectos similares previos como contexto de calibración (opcional). "
            "Se acotan a 8 para no inflar el prompt sin límite."
        ),
    )


class EstimationResponse(BaseModel):
    """Respuesta del endpoint bloqueante: texto libre + versión del prompt.

    Nota: el contrato expone `prompt_version` en snake_case en el wire (tal cual
    el spec acordado), a diferencia de la respuesta heredada `EstimateResponse`,
    que usa alias camelCase (`usedTokens`)."""

    text: str
    prompt_version: str


class SessionResponse(BaseModel):
    """Respuesta de `POST /sessions`: el identificador de la sesión recién creada.

    El cliente guarda este `session_id` (UUID v4) y lo reenvía en cada petición
    posterior para reutilizar la memoria conversacional entre páginas. Snake_case
    en el wire, igual que `EstimationResponse`."""

    session_id: str


class AttachmentInfo(BaseModel):
    """Resumen (sin contenido) de un adjunto procesado, como acuse para el cliente."""

    filename: str
    extracted_chars: int = Field(
        description="Caracteres de texto extraídos del documento (0 si no tenía texto)"
    )


class SessionEstimationResponse(BaseModel):
    """Respuesta de `POST /sessions/{id}/estimate` (path conversacional con CAG).

    No lleva `prompt_version` (este path usa el system prompt CAG, no las plantillas
    Jinja versionadas); sí el proveedor/modelo/uso, como la respuesta heredada.
    `attachments` confirma qué documentos se ingirieron y cuánto texto aportó cada
    uno (metadato, nunca el contenido). Snake_case en el wire."""

    text: str
    model: str
    provider: str
    used_tokens: int
    attachments: list[AttachmentInfo] = Field(default_factory=list)


# --- Contrato heredado: endpoint de streaming `POST /estimate/stream` ---


class EstimateRequest(BaseModel):
    transcription: str = Field(
        ...,
        min_length=1,
        description="Texto de la transcripción de la reunión con el cliente",
    )
    max_tokens: int = Field(
        4096,
        ge=512,
        le=8192,
        description="Límite de tokens de salida que el modelo puede generar",
    )


class EstimateResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    estimation: str
    model: str
    provider: str
    used_tokens: int = Field(alias="usedTokens")
    timestamp: int
