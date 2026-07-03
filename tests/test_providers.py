"""Unit tests for AnthropicProvider and helper functions."""

from __future__ import annotations

import asyncio
import queue
from unittest.mock import MagicMock

import anthropic
import pytest

from app.exceptions import LLMError
from app.providers.anthropic import (
    _SENTINEL,
    AnthropicProvider,
    _is_retryable,
)

# ---------------------------------------------------------------------------
# _is_retryable
# ---------------------------------------------------------------------------


def test_is_retryable_rate_limit() -> None:
    exc = MagicMock(spec=anthropic.RateLimitError)
    assert _is_retryable(exc) is True


def test_is_retryable_server_error() -> None:
    exc = MagicMock(spec=anthropic.APIStatusError)
    exc.status_code = 500
    assert _is_retryable(exc) is True


def test_is_retryable_client_error() -> None:
    exc = MagicMock(spec=anthropic.APIStatusError)
    exc.status_code = 400
    assert _is_retryable(exc) is False


def test_is_retryable_other_exception() -> None:
    assert _is_retryable(ValueError("foo")) is False


# ---------------------------------------------------------------------------
# AnthropicProvider.__init__
# ---------------------------------------------------------------------------


def test_provider_init_no_key() -> None:
    provider = AnthropicProvider(api_key=None, model="m", timeout=5.0)
    assert provider._client is None


def test_provider_init_with_key() -> None:
    provider = AnthropicProvider(api_key="sk-fake", model="m", timeout=5.0)
    assert provider._client is not None


# ---------------------------------------------------------------------------
# complete — no_key fast path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_complete_no_key() -> None:
    provider = AnthropicProvider(api_key=None, model="m", timeout=5.0)
    with pytest.raises(LLMError) as exc_info:
        await provider.complete("hello")
    assert exc_info.value.reason == "no_key"


# ---------------------------------------------------------------------------
# stream_complete — no_key fast path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_stream_complete_no_key() -> None:
    provider = AnthropicProvider(api_key=None, model="m", timeout=5.0)
    with pytest.raises(LLMError) as exc_info:
        async for _ in provider.stream_complete("hello"):
            pass
    assert exc_info.value.reason == "no_key"


# ---------------------------------------------------------------------------
# _get_next — queue item handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_next_string_item() -> None:
    provider = AnthropicProvider(api_key=None, model="m", timeout=5.0)
    q: queue.Queue[object] = queue.Queue()
    q.put("hello")
    loop = asyncio.get_running_loop()
    item = await provider._get_next(loop, q)
    assert item == "hello"


@pytest.mark.asyncio
async def test_get_next_sentinel() -> None:
    provider = AnthropicProvider(api_key=None, model="m", timeout=5.0)
    q: queue.Queue[object] = queue.Queue()
    q.put(_SENTINEL)
    loop = asyncio.get_running_loop()
    item = await provider._get_next(loop, q)
    assert item is _SENTINEL


@pytest.mark.asyncio
async def test_get_next_auth_error() -> None:
    provider = AnthropicProvider(api_key=None, model="m", timeout=5.0)
    q: queue.Queue[object] = queue.Queue()
    q.put(anthropic.AuthenticationError.__new__(anthropic.AuthenticationError))
    loop = asyncio.get_running_loop()
    with pytest.raises(LLMError) as exc_info:
        await provider._get_next(loop, q)
    assert exc_info.value.reason == "no_key"


@pytest.mark.asyncio
async def test_get_next_rate_limit_error() -> None:
    provider = AnthropicProvider(api_key=None, model="m", timeout=5.0)
    q: queue.Queue[object] = queue.Queue()
    q.put(anthropic.RateLimitError.__new__(anthropic.RateLimitError))
    loop = asyncio.get_running_loop()
    with pytest.raises(LLMError) as exc_info:
        await provider._get_next(loop, q)
    assert exc_info.value.reason == "rate_limit"


@pytest.mark.asyncio
async def test_get_next_api_status_error() -> None:
    provider = AnthropicProvider(api_key=None, model="m", timeout=5.0)
    q: queue.Queue[object] = queue.Queue()
    # Subclass of APIStatusError but not Auth/RateLimit
    q.put(anthropic.InternalServerError.__new__(anthropic.InternalServerError))
    loop = asyncio.get_running_loop()
    with pytest.raises(LLMError) as exc_info:
        await provider._get_next(loop, q)
    assert exc_info.value.reason == "provider_error"


@pytest.mark.asyncio
async def test_get_next_generic_exception() -> None:
    provider = AnthropicProvider(api_key=None, model="m", timeout=5.0)
    q: queue.Queue[object] = queue.Queue()
    q.put(ValueError("unexpected"))
    loop = asyncio.get_running_loop()
    with pytest.raises(LLMError) as exc_info:
        await provider._get_next(loop, q)
    assert exc_info.value.reason == "provider_error"


# ---------------------------------------------------------------------------
# _drain_queue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drain_queue_yields_chunks() -> None:
    provider = AnthropicProvider(api_key=None, model="m", timeout=5.0)
    q: queue.Queue[object] = queue.Queue()
    q.put("chunk1")
    q.put("chunk2")
    q.put(_SENTINEL)
    loop = asyncio.get_running_loop()
    chunks: list[object] = []
    async for chunk in provider._drain_queue(loop, q):
        chunks.append(chunk)
    assert chunks == ["chunk1", "chunk2"]


@pytest.mark.asyncio
async def test_drain_queue_empty() -> None:
    provider = AnthropicProvider(api_key=None, model="m", timeout=5.0)
    q: queue.Queue[object] = queue.Queue()
    q.put(_SENTINEL)
    loop = asyncio.get_running_loop()
    chunks: list[object] = []
    async for chunk in provider._drain_queue(loop, q):
        chunks.append(chunk)
    assert chunks == []
