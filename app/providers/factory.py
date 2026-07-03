"""Provider factory — selects the LLM backend from config."""

from __future__ import annotations

from app.config import Settings
from app.providers.anthropic import AnthropicProvider
from app.providers.base import LLMProvider


def create_provider(settings: Settings) -> LLMProvider:
    """Return the configured LLM provider instance.

    Uses ``settings.provider`` (a ``Literal["anthropic"]``) to select
    the backend.  Adding a new provider is a two-step process:
    write the class, then add a branch here.
    """
    if settings.provider == "anthropic":
        return AnthropicProvider(
            api_key=settings.anthropic_api_key,
            model=settings.llm_model,
            timeout=settings.llm_timeout_sec,
        )
    raise ValueError(f"Unknown provider: {settings.provider}")
