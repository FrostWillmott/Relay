"""Anthropic Claude provider: sync SDK wrapped for async use."""

from __future__ import annotations

import asyncio
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

_SENTINEL = object()  # signals end of streaming from the thread

# Matches the opening of the JSON "answer" field so we can skip the wrapper.
_ANSWER_START_RE = re.compile(r'"answer"\s*:\s*"')


def _decode_json_char(ch: str, escape_next: bool) -> tuple[str, bool]:
    """Decode one JSON string character.

    Returns ``(emitted, new_escape_next)``.  If *escape_next* is True the
    previous character was a backslash and *ch* is the escape code.
    """
    if escape_next:
        mapping = {"n": "\n", "t": "\t", "\\": "\\", '"': '"'}
        return mapping.get(ch, "\\" + ch), False
    if ch == "\\":
        return "", True
    return ch, False


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
            raise LLMError("timeout") from exc
        except anthropic.AuthenticationError as exc:
            raise LLMError("no_key") from exc
        except anthropic.RateLimitError as exc:
            raise LLMError("rate_limit") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError("provider_error") from exc

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _sync_stream(self, user_message: str, q: queue.Queue[Any]) -> None:
        """Blocking stream loop — extract answer content, put chunks into *q*.

        The model returns JSON ``{"answer": "<markdown>", "language": "ru"}``.
        A state machine skips the JSON wrapper and emits only the markdown
        content of the ``answer`` field, decoding JSON escape sequences.

        States:
          - ``waiting``: buffering raw tokens until ``"answer": "`` is found.
          - ``streaming``: emitting decoded content characters.
          - ``done``: closing quote of the answer field reached; stop early.
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
                        if ch == '"' and not escape_next:
                            # Closing quote of the answer field — done
                            if out:
                                q.put(out)
                            q.put(_SENTINEL)
                            return
                        decoded, escape_next = _decode_json_char(
                            ch, escape_next
                        )
                        out += decoded
                    if out:
                        q.put(out)
        except Exception as exc:
            q.put(exc)
        finally:
            q.put(_SENTINEL)

    async def stream_complete(self, user_message: str) -> AsyncIterator[str]:
        """Yield decoded answer-markdown chunks from a streaming API call.

        A per-chunk deadline of *self._timeout* seconds ensures the stream
        never hangs indefinitely on a stalled connection.
        """
        if self._client is None:
            raise LLMError("no_key")
        q: queue.Queue[Any] = queue.Queue()
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, self._sync_stream, user_message, q)
        while True:
            item = await self._get_next(loop, q)
            if item is _SENTINEL:
                return
            yield item  # type: ignore[misc]

    async def _get_next(
        self, loop: asyncio.AbstractEventLoop, q: queue.Queue[Any]
    ) -> object:
        """Fetch one item from *q* with timeout; raise LLMError on failure."""
        try:
            item = await asyncio.wait_for(
                loop.run_in_executor(None, q.get),
                timeout=self._timeout,
            )
        except TimeoutError as exc:
            raise LLMError("timeout") from exc
        if isinstance(item, anthropic.AuthenticationError):
            raise LLMError("no_key") from item
        if isinstance(item, anthropic.RateLimitError):
            raise LLMError("rate_limit") from item
        if isinstance(item, (anthropic.APIStatusError, Exception)) and (
            item is not _SENTINEL
        ):
            raise LLMError("provider_error") from item
        return item
