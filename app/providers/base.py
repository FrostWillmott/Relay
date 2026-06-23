"""LLM provider abstraction."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol


class LLMProvider(Protocol):
    """Interface every LLM backend must implement."""

    async def complete(self, user_message: str) -> str:
        """Send a user message and return the raw text response."""
        ...

    async def stream_complete(self, user_message: str) -> AsyncIterator[str]:
        """Yield raw text chunks as they arrive from the provider."""
        return
        yield  # makes this an async generator in the Protocol stub
