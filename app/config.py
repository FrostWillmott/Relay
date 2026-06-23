"""Application settings loaded from environment / .env file."""

from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed settings; missing required fields surface as errors at startup."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM provider key — optional, so the app starts even without it;
    # a missing key surfaces as a 503 on the first request.
    anthropic_api_key: str | None = None
    llm_model: str = "claude-haiku-4-5"
    llm_timeout_sec: float = 30.0
    max_input_len: int = 2000
    provider: Literal["anthropic"] = "anthropic"


settings = Settings()
