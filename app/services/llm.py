"""LLM service: sanitize input, call provider, validate output."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Literal

from pydantic import ValidationError

from app.config import settings
from app.exceptions import EmptyInputError, LLMError
from app.models.response import AskResponse, HistoryItem, LLMOutput
from app.prompts import build_user_message
from app.providers.base import LLMProvider
from app.services import history as history_service

# Public alias so routers don't need to import from prompts directly.
build_message = build_user_message

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
    text = text[: settings.max_input_len]
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


async def ask_stream_llm(
    question: str, provider: LLMProvider
) -> AsyncIterator[str | AskResponse]:
    """Stream LLM answer chunks, then yield a final AskResponse.

    Yields decoded markdown string chunks as the model generates them.
    The final item is an :class:`AskResponse` with the assembled answer
    and heuristically detected language (Cyrillic → ``"ru"``).

    Raises :class:`EmptyInputError` on blank input before any I/O.
    Propagates :class:`LLMError` on provider failures.
    """
    if not question.strip():
        raise EmptyInputError("Question must not be empty")
    sanitized = sanitize(question)
    user_msg = build_user_message(sanitized)

    answer_chunks: list[str] = []
    async for chunk in provider.stream_complete(user_msg):
        answer_chunks.append(chunk)
        yield chunk

    full_answer = "".join(answer_chunks)
    if not full_answer:
        raise LLMError("invalid_output")

    has_cyrillic = any("\u0400" <= c <= "\u04ff" for c in full_answer)
    language: Literal["ru", "en"] = "ru" if has_cyrillic else "en"

    item = HistoryItem(
        question=question, answer=full_answer, language=language
    )
    history_service.append(item)

    yield AskResponse(answer=full_answer, language=language)
