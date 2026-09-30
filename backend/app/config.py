from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment configuration (see .env.example). Read from the process env, then `.env`."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    groq_api_key: str | None = None
    database_url: str | None = None
    model_understanding: str = "openai/gpt-oss-20b"
    model_answer: str = "openai/gpt-oss-120b"
    # Comma-separated list, e.g. "https://app.vercel.app,http://localhost:3000".
    allowed_origins: str = "http://localhost:3000"
    admin_token: str | None = None

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
