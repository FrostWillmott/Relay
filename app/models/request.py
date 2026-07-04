"""Request DTOs."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from app.config import settings


class AskRequest(BaseModel):
    """Payload for POST /ask."""

    question: str = Field(
        max_length=settings.max_input_len,
        description="User question (service layer handles truncation)",
    )

    @field_validator("question")
    @classmethod
    def not_empty(cls, v: str) -> str:
        """Reject blank questions early."""
        if not v.strip():
            raise ValueError("question must not be empty")
        return v.strip()
