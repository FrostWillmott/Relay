"""Anthropic Claude provider: sync SDK wrapped for async use."""

from __future__ import annotations

import asyncio
import logging
import queue
import re
from collections.abc import AsyncIterator
from typing import Any

import anthropic
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.exceptions import LLMError
from app.prompts import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

_SENTINEL = object()  # signals end of streaming from the thread

# Matches the opening of the JSON "answer" field so we can skip the wrapper.
_ANSWER_START_RE = re.compile(r'"answer"\s*:\s*"')


def _decode_json_char(
    ch: str, escape_next: bool, unicode_buf: str | None = None
) -> tuple[str, bool, str | None]:
    r"""Decode one JSON string character, including ``\uXXXX`` escapes.

    Returns ``(emitted, new_escape_next, new_unicode_buf)``.
    *unicode_buf* is ``None`` when not inside a ``\uXXXX`` sequence, or a
    0-3 character hex string while accumulating digits.
    """
    if unicode_buf is not None:
        new_buf = unicode_buf + ch
        if len(new_buf) == 4:
            try:
                return chr(int(new_buf, 16)), False, None
            except ValueError:
                return "\\u" + new_buf, False, None
        return "", False, new_buf
    if escape_next:
        if ch == "u":
            return "", False, ""  # begin collecting 4 hex digits
        mapping = {
            "n": "\n",
            "t": "\t",
            "r": "\r",
            "b": "\b",
            "f": "\f",
            "\\": "\\",
            '"': '"',
            "/": "/",
        }
        return mapping.get(ch, "\\" + ch), False, None
    if ch == "\\":
        return "", True, None
    return ch, False, None


def _is_retryable(exc: BaseException) -> bool:
    """Return True only for transient failures (429, 5xx)."""
    if isinstance(exc, anthropic.RateLimitError):
        return True
    if isinstance(exc, anthropic.APIStatusError) and exc.status_code >= 500:
        return True
    return False


class AnthropicProvider:
    """Wraps the sync Anthropic client for use inside an async application."""

    def __init__(
        self, api_key: str | None, model: str, timeout: float
    ) -> None:
        """Store config; defer client creation.

        A missing API key only surfaces as an error on the first request.
        """
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._client: anthropic.Anthropic | None = (
            anthropic.Anthropic(api_key=api_key)
            if api_key is not None
            else None
        )

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _sync_call(self, user_message: str) -> str:
        """Blocking API call — runs inside a thread via asyncio.to_thread."""
        assert self._client is not None  # guarded by complete()
        response = self._client.messages.create(
            model=self._model,
            max_tokens=4096,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_message}],
        )
        if not response.content:
            raise LLMError("invalid_output")
        block = response.content[0]
        if not isinstance(block, anthropic.types.TextBlock):
            raise LLMError("invalid_output")
        return block.text

    async def complete(self, user_message: str) -> str:
        """Send a user message asynchronously, with timeout and retry."""
        if self._client is None:
            raise LLMError("no_key")
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._sync_call, user_message),
                timeout=self._timeout,
            )
        except TimeoutError as exc:
            logger.warning("LLM call timed out after %.1fs", self._timeout)
            raise LLMError("timeout") from exc
        except anthropic.AuthenticationError as exc:
            logger.critical("Anthropic authentication failed (bad API key?)")
            raise LLMError("no_key") from exc
        except anthropic.RateLimitError as exc:
            logger.warning("Anthropic rate limit hit (429)")
            raise LLMError("rate_limit") from exc
        except anthropic.APIStatusError as exc:
            logger.error(
                "Anthropic API error: status=%d body=%s",
                exc.status_code,
                exc.body,
            )
            raise LLMError("provider_error") from exc

    def _sync_stream(self, user_message: str, q: queue.Queue[Any]) -> None:
        """Blocking stream loop — extract answer content, put chunks into *q*.

        The model returns JSON ``{"answer": "<markdown>", "language": "ru"}``.
        A state machine skips the JSON wrapper and emits only the markdown
        content of the ``answer`` field, decoding JSON escape sequences.

        States:
          - ``waiting``: buffering raw tokens until ``"answer": "`` is found.
          - ``streaming``: emitting decoded content characters.
          - ``done``: closing quote of the answer field reached; stop early.

        Exceptions are placed into *q* so the async consumer can re-raise
        them; the ``@retry`` decorator cannot be applied here because the
        ``except`` block swallows exceptions into the queue before they can
        propagate to tenacity.  Retry logic lives in :meth:`stream_complete`.
        """
        assert self._client is not None
        try:
            with self._client.messages.stream(
                model=self._model,
                max_tokens=4096,
                system=[
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": user_message}],
            ) as stream:
                buf = ""  # accumulation buffer before "answer": "
                in_answer = False
                escape_next = False
                unicode_buf: str | None = None

                for text in stream.text_stream:
                    if not in_answer:
                        buf += text
                        m = _ANSWER_START_RE.search(buf)
                        if not m:
                            # Keep only a small suffix for multi-chunk patterns
                            buf = buf[-30:]
                            continue
                        # Found the opening quote; pending content is after it
                        in_answer = True
                        pending = buf[m.end() :]
                    else:
                        # Already past the opening quote; process new token
                        pending = text

                    # Emit decoded chars from pending
                    out = ""
                    for ch in pending:
                        if (
                            ch == '"'
                            and not escape_next
                            and unicode_buf is None
                        ):
                            # Closing quote of the answer field — done
                            if out:
                                q.put(out)
                            q.put(_SENTINEL)
                            return
                        decoded, escape_next, unicode_buf = _decode_json_char(
                            ch, escape_next, unicode_buf
                        )
                        out += decoded
                    if out:
                        q.put(out)
        except Exception as exc:
            logger.exception("Stream producer failed with unexpected error")
            q.put(exc)
        finally:
            q.put(_SENTINEL)

    async def stream_complete(self, user_message: str) -> AsyncIterator[str]:
        """Yield decoded answer-markdown chunks from a streaming API call.

        Retry is applied at this level: on a transient failure (429 / 5xx)
        the whole stream restarts from scratch, up to 3 attempts.  A per-chunk
        deadline of *self._timeout* seconds prevents indefinite hangs.
        """
        if self._client is None:
            raise LLMError("no_key")

        last_exc: LLMError | None = None
        for attempt in range(3):
            q: queue.Queue[Any] = queue.Queue()
            loop = asyncio.get_running_loop()
            loop.run_in_executor(None, self._sync_stream, user_message, q)
            try:
                async for chunk in self._drain_queue(loop, q):
                    yield chunk
                return  # success — stop retrying
            except LLMError as exc:
                # _get_next wraps SDK exceptions in LLMError; check reason
                if (
                    exc.reason not in ("rate_limit", "provider_error")
                    or attempt == 2
                ):
                    raise
                last_exc = exc
                # Exponential back-off: 1 s, 2 s before the third attempt.
                await asyncio.sleep(2**attempt)

        if last_exc is not None:
            raise last_exc  # pragma: no cover

    async def _drain_queue(
        self, loop: asyncio.AbstractEventLoop, q: queue.Queue[Any]
    ) -> AsyncIterator[str]:
        """Yield string chunks from *q* until sentinel; raise on errors."""
        while True:
            item = await self._get_next(loop, q)
            if item is _SENTINEL:
                return
            yield item  # type: ignore[misc]

    async def _get_next(
        self, loop: asyncio.AbstractEventLoop, q: queue.Queue[Any]
    ) -> object:
        """Fetch one item from *q* with timeout; raise LLMError on failure."""
        # Use a slightly longer timeout on q.get so the thread unblocks and
        # returns the pool slot even if asyncio.wait_for fires first.
        timeout = self._timeout

        def _blocking_get() -> object:
            try:
                return q.get(timeout=timeout + 1.0)
            except queue.Empty:
                return _SENTINEL

        try:
            item = await asyncio.wait_for(
                loop.run_in_executor(None, _blocking_get),
                timeout=self._timeout,
            )
        except TimeoutError as exc:
            logger.warning("Stream chunk timed out after %.1fs", self._timeout)
            raise LLMError("timeout") from exc
        if isinstance(item, anthropic.AuthenticationError):
            logger.critical("Stream auth error from thread")
            raise LLMError("no_key") from item
        if isinstance(item, anthropic.RateLimitError):
            logger.warning("Stream rate limit from thread (429)")
            raise LLMError("rate_limit") from item
        if isinstance(item, anthropic.APIStatusError):
            logger.error(
                "Stream API error from thread: status=%s body=%s",
                getattr(item, "status_code", "?"),
                getattr(item, "body", "?"),
            )
            raise LLMError("provider_error") from item
        if isinstance(item, Exception):
            logger.exception("Unexpected exception from stream thread")
            raise LLMError("provider_error") from item
        return item
