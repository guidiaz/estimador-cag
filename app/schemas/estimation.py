from pydantic import BaseModel, ConfigDict, Field


class EstimateRequest(BaseModel):
    transcription: str = Field(
        ...,
        min_length=1,
        description="Texto de la transcripción de la reunión con el cliente",
    )


class EstimateResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    estimation: str
    model: str
    provider: str
    used_tokens: int = Field(alias="usedTokens")
    timestamp: int
