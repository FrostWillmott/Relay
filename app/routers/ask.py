"""HTTP router for /ask and /history — no business logic here."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.exceptions import EmptyInputError, LLMError
from app.models.request import AskRequest
from app.models.response import AskResponse, HistoryItem
from app.providers.base import LLMProvider
from app.services import history as history_service
from app.services import llm as llm_service

router = APIRouter()


def _get_provider(request: Request) -> LLMProvider:
    return request.app.state.provider  # type: ignore[no-any-return]


def _llm_exc_to_http(exc: LLMError) -> HTTPException:
    _status: dict[str, int] = {
        "no_key": 503,
        "timeout": 504,
        "rate_limit": 429,
        "invalid_output": 502,
        "provider_error": 502,
    }
    return HTTPException(
        status_code=_status.get(exc.reason, 502), detail=exc.reason
    )


@router.post("/ask", response_model=AskResponse)
async def ask(body: AskRequest, request: Request) -> AskResponse:
    """Send a question to the LLM and return a structured answer."""
    provider = _get_provider(request)
    try:
        output = await llm_service.ask_llm(body.question, provider)
    except EmptyInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LLMError as exc:
        raise _llm_exc_to_http(exc) from exc
    item = HistoryItem(
        question=body.question,
        answer=output.answer,
        language=output.language,
    )
    history_service.append(item)
    return AskResponse(answer=output.answer, language=output.language)


@router.post("/ask/stream")
async def ask_stream(body: AskRequest, request: Request) -> StreamingResponse:
    """Stream the LLM answer as Server-Sent Events.

    The provider now yields decoded markdown chunks (not raw JSON tokens),
    so each ``chunk`` event is clean text ready for incremental rendering.
    When the stream ends, a ``done`` event carries the full answer and
    language (detected heuristically from the assembled text).
    """
    provider = _get_provider(request)

    async def _sse_generator() -> AsyncGenerator[str, None]:
        answer_chunks: list[str] = []
        try:
            if not body.question.strip():
                yield f"data: {json.dumps({'error': 'empty'})}\n\n"
                return
            sanitized = llm_service.sanitize(body.question)
            user_message = llm_service.build_message(sanitized)
            async for chunk in provider.stream_complete(user_message):
                answer_chunks.append(chunk)
                payload = json.dumps({"chunk": chunk}, ensure_ascii=False)
                yield f"data: {payload}\n\n"
        except LLMError as exc:
            yield f"data: {json.dumps({'error': exc.reason})}\n\n"
            return

        # The stream yields only the answer markdown; language comes from
        # a lightweight parse of the full assembled text (no extra API call).
        full_answer = "".join(answer_chunks)
        if not full_answer:
            yield f"data: {json.dumps({'error': 'invalid_output'})}\n\n"
            return

        # Detect language: Cyrillic characters present → ru, else en.
        has_cyrillic = any("\u0400" <= c <= "\u04ff" for c in full_answer)
        language: Literal["ru", "en"] = "ru" if has_cyrillic else "en"

        item = HistoryItem(
            question=body.question,
            answer=full_answer,
            language=language,
        )
        history_service.append(item)
        done_payload = json.dumps(
            {
                "done": True,
                "answer": full_answer,
                "language": language,
            },
            ensure_ascii=False,
        )
        yield f"data: {done_payload}\n\n"

    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/history", response_model=list[HistoryItem])
async def history() -> list[HistoryItem]:
    """Return the last 5 queries."""
    return history_service.get_all()
