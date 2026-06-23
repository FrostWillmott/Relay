"""Response DTOs and internal parse targets."""

from __future__ import annotations

import time
from typing import Literal

from pydantic import BaseModel, Field


class LLMOutput(BaseModel):
    """Internal: parsed JSON envelope returned by the LLM."""

    answer: str
    language: Literal["ru", "en"]


class AskResponse(BaseModel):
    """Public response for POST /ask."""

    answer: str
    language: Literal["ru", "en"]


class HistoryItem(BaseModel):
    """Single entry in the query history."""

    question: str
    answer: str
    language: Literal["ru", "en"]
    timestamp: float = Field(default_factory=time.time)
