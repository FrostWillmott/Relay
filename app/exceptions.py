"""Domain exceptions for the Relay application."""

from __future__ import annotations


class EmptyInputError(ValueError):
    """Raised when the user submits an empty question."""


class LLMError(RuntimeError):
    """Raised for all LLM-related failures; carries a reason code."""

    def __init__(self, reason: str) -> None:
        """Initialize with a short reason code."""
        super().__init__(reason)
        self.reason = reason
