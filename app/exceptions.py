"""Domain exceptions for the Relay application."""

from __future__ import annotations

from enum import StrEnum


class LLMErrorReason(StrEnum):
    """Typed reason codes for :exc:`LLMError`.

    Using an enum instead of raw strings lets mypy catch typos in
    ``exc.reason ==`` comparisons and dict lookups.
    """

    NO_KEY = "no_key"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    INVALID_OUTPUT = "invalid_output"
    PROVIDER_ERROR = "provider_error"


class EmptyInputError(ValueError):
    """Raised when the user submits an empty question."""


class LLMError(RuntimeError):
    """Raised for all LLM-related failures; carries a reason code."""

    def __init__(self, reason: LLMErrorReason | str) -> None:
        """Initialize with a reason code (enum member or raw string)."""
        reason_str = str(reason)
        super().__init__(reason_str)
        self.reason = reason_str  # stored as str for JSON-serialisability
