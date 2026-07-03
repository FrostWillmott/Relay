"""Anthropic Claude provider using the native async client."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

import anthropic

from app.exceptions import LLMError
from app.prompts import SYSTEM_PROMPT

logger = logging.getLogger(__name__)


def _is_retryable(exc: BaseException) -> bool:
    """Return True only for transient failures (429, 5xx)."""
    if isinstance(exc, anthropic.RateLimitError):
        return True
    if isinstance(exc, anthropic.APIStatusError) and exc.status_code >= 500:
        return True
    return False


class AnthropicProvider:
    """Wraps the async Anthropic client."""

    def __init__(
        self, api_key: str | None, model: str, timeout: float
    ) -> None:
        """Store config; defer client creation.

        A missing API key surfaces as ``LLMError("no_key")`` on first use.
        """
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._client: anthropic.AsyncAnthropic | None = (
            anthropic.AsyncAnthropic(api_key=api_key)
            if api_key is not None
            else None
        )

    @staticmethod
    def _map_exc(exc: BaseException, attempt: int, timeout: float) -> bool:
        """Map an API exception to an LLMError or signal retry.

        Returns ``True`` if the caller should sleep and retry.
        Raises :exc:`LLMError` for non-retryable conditions or on the
        final attempt.
        """
        if isinstance(exc, TimeoutError):
            logger.warning("Call timed out after %.1fs", timeout)
            raise LLMError("timeout") from exc
        if isinstance(exc, anthropic.AuthenticationError):
            logger.critical("Anthropic authentication failed (bad API key?)")
            raise LLMError("no_key") from exc
        if isinstance(exc, anthropic.RateLimitError):
            logger.warning("Rate limit (429) attempt %d/3", attempt + 1)
            if attempt >= 2:
                raise LLMError("rate_limit") from exc
            return True
        if isinstance(exc, anthropic.APIStatusError):
            if exc.status_code < 500:
                logger.error(
                    "Client error: status=%d body=%s",
                    exc.status_code,
                    exc.body,
                )
                raise LLMError("provider_error") from exc
            logger.warning(
                "Server error: status=%d attempt %d/3",
                exc.status_code,
                attempt + 1,
            )
            if attempt >= 2:
                raise LLMError("provider_error") from exc
            return True
        raise  # unexpected — let it propagate

    async def complete(self, user_message: str) -> str:
        """Send a user message asynchronously, with timeout and retry.

        Retries on transient failures (429, 5xx) up to 3 attempts with
        exponential back-off.  Non-retryable 4xx errors and timeouts are
        raised immediately.
        """
        if self._client is None:
            raise LLMError("no_key")

        for attempt in range(3):
            try:
                response = await asyncio.wait_for(
                    self._client.messages.create(
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
                    ),
                    timeout=self._timeout,
                )
            except BaseException as exc:
                if self._map_exc(exc, attempt, self._timeout):
                    await asyncio.sleep(2**attempt)
                    continue
                raise

            if not response.content:
                raise LLMError("invalid_output")
            block = response.content[0]
            if not isinstance(block, anthropic.types.TextBlock):
                raise LLMError("invalid_output")
            return block.text

        raise RuntimeError("unreachable")  # pragma: no cover

    async def stream_complete(self, user_message: str) -> AsyncIterator[str]:
        """Yield raw text chunks from a streaming API call.

        The caller receives the model's output as it arrives.
        No JSON extraction is performed — the service layer is responsible
        for transforming the raw stream into answer-markdown chunks.

        Retries on transient failures (429, 5xx) up to 3 attempts with
        exponential back-off.  Already-yielded chunks may be duplicated on
        retry; the caller should handle this or accept the rare edge case.
        """
        if self._client is None:
            raise LLMError("no_key")

        for attempt in range(3):
            try:
                async with self._client.messages.stream(
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
                    async for text in stream.text_stream:
                        yield text
                return  # success — stop retrying
            except BaseException as exc:
                if self._map_exc(exc, attempt, self._timeout):
                    await asyncio.sleep(2**attempt)
                    continue
                raise
