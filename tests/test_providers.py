"""Unit tests for AnthropicProvider and helper functions."""

from __future__ import annotations

from unittest.mock import MagicMock

import anthropic
import pytest

from app.exceptions import LLMError
from app.providers.anthropic import AnthropicProvider, _is_retryable

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
