"""HTTP router for /ask and /history — no business logic here."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncGenerator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.exceptions import EmptyInputError, LLMError
from app.models.request import AskRequest
from app.models.response import AskResponse, HistoryItem
from app.providers.base import LLMProvider
from app.services import history as history_service
from app.services import llm as llm_service

logger = logging.getLogger(__name__)
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
    started = time.monotonic()
    try:
        output = await llm_service.ask_llm(body.question, provider)
    except EmptyInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LLMError as exc:
        logger.warning(
            "POST /ask failed: reason=%s question=%.80r elapsed=%.3fs",
            exc.reason,
            body.question,
            time.monotonic() - started,
        )
        raise _llm_exc_to_http(exc) from exc
    item = HistoryItem(
        question=body.question,
        answer=output.answer,
        language=output.language,
    )
    history_service.append(item)
    logger.info(
        "POST /ask OK: len(answer)=%d language=%s elapsed=%.3fs",
        len(output.answer),
        output.language,
        time.monotonic() - started,
    )
    return AskResponse(answer=output.answer, language=output.language)


@router.post("/ask/stream")
async def ask_stream(body: AskRequest, request: Request) -> StreamingResponse:
    """Stream the LLM answer as Server-Sent Events.

    Each ``chunk`` event carries clean decoded markdown (not raw JSON).
    The final ``done`` event carries the assembled answer and language.
    Business logic lives in :func:`llm_service.ask_stream_llm`.
    """
    provider = _get_provider(request)

    async def _sse_generator() -> AsyncGenerator[str, None]:
        started = time.monotonic()
        try:
            async for item in llm_service.ask_stream_llm(
                body.question, provider
            ):
                if isinstance(item, AskResponse):
                    done_payload = json.dumps(
                        {
                            "done": True,
                            "answer": item.answer,
                            "language": item.language,
                        },
                        ensure_ascii=False,
                    )
                    yield f"data: {done_payload}\n\n"
                    logger.info(
                        "POST /ask/stream OK: len=%d lang=%s %.3fs",
                        len(item.answer),
                        item.language,
                        time.monotonic() - started,
                    )
                else:
                    payload = json.dumps({"chunk": item}, ensure_ascii=False)
                    yield f"data: {payload}\n\n"
        except EmptyInputError:
            yield f"data: {json.dumps({'error': 'empty'})}\n\n"
        except LLMError as exc:
            logger.warning(
                "POST /ask/stream failed: %s q=%.80r %.3fs",
                exc.reason,
                body.question,
                time.monotonic() - started,
            )
            yield f"data: {json.dumps({'error': exc.reason})}\n\n"

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
