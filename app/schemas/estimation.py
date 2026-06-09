from pydantic import BaseModel, ConfigDict, Field


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
