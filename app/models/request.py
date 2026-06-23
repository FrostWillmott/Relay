"""Request DTOs."""

from __future__ import annotations

from pydantic import BaseModel, field_validator


class AskRequest(BaseModel):
    """Payload for POST /ask."""

    question: str

    @field_validator("question")
    @classmethod
    def not_empty(cls, v: str) -> str:
        """Reject blank questions early."""
        if not v.strip():
            raise ValueError("question must not be empty")
        return v.strip()
