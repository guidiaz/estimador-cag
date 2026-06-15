from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: str = ""
    anthropic_api_key: str = ""
    llm_provider: str = "openai"
    llm_model: str = ""

    # Router LiteLLM: modelo primario (por defecto) y de fallback, en formato
    # litellm "<proveedor>/<modelo>".
    primary_model: str = "anthropic/claude-haiku-4-5"
    fallback_model: str = "openai/gpt-4o-mini"

    redis_url: str = "redis://localhost:6379/0"
    cache_enabled: bool = True
    cache_ttl_seconds: int = 86400  # 24h

    @property
    def resolved_model(self) -> str:
        if self.llm_model:
            return self.llm_model
        if self.llm_provider == "anthropic":
            return "claude-haiku-4-5"
        return "gpt-4o-mini"


settings = Settings()
