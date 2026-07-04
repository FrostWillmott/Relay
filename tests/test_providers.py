"""Unit tests for AnthropicProvider and helper functions."""

from __future__ import annotations

from unittest.mock import MagicMock

import anthropic
import pytest

from app.exceptions import LLMError, LLMErrorReason
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
# _map_exc
# ---------------------------------------------------------------------------


def _make_exc(base_cls: type[BaseException], **attrs: object) -> BaseException:
    """Create a minimal real Exception subclass instance.

    Uses ``__new__`` + ``Exception.__init__`` to skip the anthropic
    constructor (which requires a live response object), while still
    passing both ``isinstance(exc, base_cls)`` and the ``raise ... from
    exc`` chain check.
    """
    cls = type(f"_Mock{base_cls.__name__}", (base_cls,), {})
    exc = cls.__new__(cls)
    Exception.__init__(exc, base_cls.__name__)
    for k, v in attrs.items():
        object.__setattr__(exc, k, v)
    return exc


def test_map_exc_timeout_error() -> None:
    with pytest.raises(LLMError) as exc_info:
        AnthropicProvider._map_exc(TimeoutError("timed out"), 0, 5.0)
    assert exc_info.value.reason == LLMErrorReason.TIMEOUT


def test_map_exc_authentication_error() -> None:
    exc = _make_exc(anthropic.AuthenticationError)
    with pytest.raises(LLMError) as exc_info:
        AnthropicProvider._map_exc(exc, 0, 5.0)
    assert exc_info.value.reason == LLMErrorReason.NO_KEY


def test_map_exc_rate_limit_retry() -> None:
    exc = _make_exc(anthropic.RateLimitError)
    assert AnthropicProvider._map_exc(exc, 0, 5.0) is True


def test_map_exc_rate_limit_final() -> None:
    exc = _make_exc(anthropic.RateLimitError)
    with pytest.raises(LLMError) as exc_info:
        AnthropicProvider._map_exc(exc, 2, 5.0)
    assert exc_info.value.reason == LLMErrorReason.RATE_LIMIT


def test_map_exc_connection_error_retry() -> None:
    exc = _make_exc(anthropic.APIConnectionError)
    assert AnthropicProvider._map_exc(exc, 0, 5.0) is True


def test_map_exc_connection_error_final() -> None:
    exc = _make_exc(anthropic.APIConnectionError)
    with pytest.raises(LLMError) as exc_info:
        AnthropicProvider._map_exc(exc, 2, 5.0)
    assert exc_info.value.reason == LLMErrorReason.PROVIDER_ERROR


def test_map_exc_client_error_4xx() -> None:
    """4xx errors should NOT be retried — they're client mistakes."""
    exc = _make_exc(
        anthropic.APIStatusError, status_code=400, body="Bad request"
    )
    with pytest.raises(LLMError) as exc_info:
        AnthropicProvider._map_exc(exc, 0, 5.0)
    assert exc_info.value.reason == LLMErrorReason.PROVIDER_ERROR


def test_map_exc_server_error_5xx_retry() -> None:
    exc = _make_exc(anthropic.APIStatusError, status_code=500)
    assert AnthropicProvider._map_exc(exc, 0, 5.0) is True


def test_map_exc_server_error_5xx_final() -> None:
    exc = _make_exc(anthropic.APIStatusError, status_code=503)
    with pytest.raises(LLMError) as exc_info:
        AnthropicProvider._map_exc(exc, 2, 5.0)
    assert exc_info.value.reason == LLMErrorReason.PROVIDER_ERROR


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
