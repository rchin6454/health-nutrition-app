from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment configuration (see .env.example). Read from the process env, then `.env`."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    groq_api_key: str | None = None
    database_url: str | None = None
    model_understanding: str = "openai/gpt-oss-20b"
    model_answer: str = "openai/gpt-oss-120b"
    # Reasoning tokens count against Groq's token limits; "low" is enough for classification.
    reasoning_effort_understanding: Literal["low", "medium", "high"] = "low"
    reasoning_effort_answer: Literal["low", "medium", "high"] = "medium"
    # Groq limits, applied to each model separately (Groq counts them per model).
    # Defaults are the free-tier limits of openai/gpt-oss-20b and openai/gpt-oss-120b.
    groq_rpm: int = 30  # requests per minute
    groq_rpd: int = 1000  # requests per day
    groq_tpm: int = 8000  # tokens per minute
    groq_tpd: int = 200_000  # tokens per day
    # How long a call may wait for the per-minute budget before it is refused instead.
    llm_max_wait_s: float = 8.0
    # Passage retrieval: fastembed model (384 dimensions) and where its files are cached.
    # The Dockerfile downloads the model into EMBEDDING_CACHE_DIR at build time.
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_cache_dir: str | None = None
    # Comma-separated list, e.g. "https://app.vercel.app,http://localhost:3000".
    allowed_origins: str = "http://localhost:3000"
    admin_token: str | None = None

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
