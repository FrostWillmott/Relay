"""LLM service: sanitize input, call provider, validate output."""

from __future__ import annotations

import json
import re

from pydantic import ValidationError

from app.exceptions import EmptyInputError, LLMError
from app.models.response import LLMOutput
from app.prompts import build_user_message
from app.providers.base import LLMProvider

# Public alias so routers don't need to import from prompts directly.
build_message = build_user_message

_MAX_INPUT_LEN = 2000

_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"ignore\s+(?:previous|above|all)\s+(?:instructions?|prompts?)",
        re.IGNORECASE,
    ),
    re.compile(r"SYSTEM\s*:", re.IGNORECASE),
    re.compile(r"</?(?:SYSTEM|INST|SYS)>", re.IGNORECASE),
    re.compile(r"```\s*(?:system|instructions?)\b", re.IGNORECASE),
]


def sanitize(text: str) -> str:
    """Truncate and neutralize prompt-injection markers."""
    text = text[:_MAX_INPUT_LEN]
    for pattern in _INJECTION_PATTERNS:
        text = pattern.sub("[REMOVED]", text)
    return text


def parse_output(raw: str) -> LLMOutput:
    """Parse JSON from raw LLM text, stripping markdown fences if present."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(
            ln for ln in lines if not ln.startswith("```")
        ).strip()
    data = json.loads(text)
    return LLMOutput.model_validate(data)


async def _validate_output(raw: str, provider: LLMProvider) -> LLMOutput:
    """Validate raw LLM output; attempt one JSON repair on failure."""
    try:
        return parse_output(raw)
    except (json.JSONDecodeError, ValidationError):
        repair_msg = (
            "Твой предыдущий ответ не является валидным JSON.\n"
            f"Исходный ответ: {raw!r}\n\n"
            "Верни только JSON-объект без markdown-обёртки:\n"
            '{"answer": "...", "language": "ru" | "en"}'
        )
        raw2 = await provider.complete(repair_msg)
        try:
            return parse_output(raw2)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise LLMError("invalid_output") from exc


async def ask_llm(question: str, provider: LLMProvider) -> LLMOutput:
    """End-to-end: sanitize question, call LLM, return validated output."""
    if not question.strip():
        raise EmptyInputError("Question must not be empty")
    sanitized = sanitize(question)
    user_msg = build_user_message(sanitized)
    raw = await provider.complete(user_msg)
    return await _validate_output(raw, provider)
