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


class EstimationResponse(BaseModel):
    """Respuesta del endpoint bloqueante: texto libre + versión del prompt.

    Nota: el contrato expone `prompt_version` en snake_case en el wire (tal cual
    el spec acordado), a diferencia de la respuesta heredada `EstimateResponse`,
    que usa alias camelCase (`usedTokens`)."""

    text: str
    prompt_version: str


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
