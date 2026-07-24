"""Unit tests for AnthropicProvider and helper functions."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
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


# ---------------------------------------------------------------------------
# Fake SDK client — stands in for AsyncAnthropic in provider tests
# ---------------------------------------------------------------------------

_HANG = object()  # sentinel: create() blocks forever (for timeout tests)


def _text_response(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        content=[anthropic.types.TextBlock(type="text", text=text)]
    )


class _FakeStream:
    """Async context manager mimicking the SDK's MessageStream.

    ``enter_exc`` raises before any chunk (retryable window);
    ``exc`` raises after all chunks are yielded (mid-stream failure).
    """

    def __init__(
        self,
        chunks: list[str] | None = None,
        exc: Exception | None = None,
        enter_exc: Exception | None = None,
    ) -> None:
        self._chunks = chunks or []
        self._exc = exc
        self._enter_exc = enter_exc

    async def __aenter__(self) -> _FakeStream:
        if self._enter_exc is not None:
            raise self._enter_exc
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    @property
    def text_stream(self) -> AsyncIterator[str]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[str]:
        for chunk in self._chunks:
            yield chunk
        if self._exc is not None:
            raise self._exc


class _FakeMessages:
    """Scripted ``messages.create`` / ``messages.stream``.

    Each call consumes the next scripted result; an Exception result
    is raised, ``_HANG`` blocks forever, anything else is returned.
    """

    def __init__(
        self,
        create_results: list[object] | None = None,
        stream_results: list[_FakeStream] | None = None,
    ) -> None:
        self._create_results = list(create_results or [])
        self._stream_results = list(stream_results or [])
        self.create_calls = 0
        self.stream_calls = 0

    async def create(self, **kwargs: object) -> object:
        self.create_calls += 1
        result = self._create_results.pop(0)
        if result is _HANG:
            await asyncio.Event().wait()
        if isinstance(result, Exception):
            raise result
        return result

    def stream(self, **kwargs: object) -> _FakeStream:
        self.stream_calls += 1
        return self._stream_results.pop(0)


class _FakeClient:
    def __init__(self, messages: _FakeMessages) -> None:
        self.messages = messages


def make_provider(
    messages: _FakeMessages, timeout: float = 5.0
) -> AnthropicProvider:
    provider = AnthropicProvider(api_key="sk-fake", model="m", timeout=timeout)
    provider._client = _FakeClient(messages)  # type: ignore[assignment]
    return provider


@pytest.fixture
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _instant(_: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _instant)


# ---------------------------------------------------------------------------
# complete — full paths via fake client
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_happy_path() -> None:
    messages = _FakeMessages(create_results=[_text_response("hello!")])
    provider = make_provider(messages)
    assert await provider.complete("hi") == "hello!"
    assert messages.create_calls == 1


@pytest.mark.asyncio
@pytest.mark.usefixtures("no_backoff")
async def test_complete_retries_rate_limit_then_succeeds() -> None:
    messages = _FakeMessages(
        create_results=[
            _make_exc(anthropic.RateLimitError),
            _text_response("ok"),
        ]
    )
    provider = make_provider(messages)
    assert await provider.complete("hi") == "ok"
    assert messages.create_calls == 2


@pytest.mark.asyncio
@pytest.mark.usefixtures("no_backoff")
async def test_complete_retry_exhausted() -> None:
    messages = _FakeMessages(
        create_results=[
            _make_exc(anthropic.APIStatusError, status_code=503)
            for _ in range(3)
        ]
    )
    provider = make_provider(messages)
    with pytest.raises(LLMError) as exc_info:
        await provider.complete("hi")
    assert exc_info.value.reason == LLMErrorReason.PROVIDER_ERROR
    assert messages.create_calls == 3


@pytest.mark.asyncio
async def test_complete_client_error_no_retry() -> None:
    messages = _FakeMessages(
        create_results=[
            _make_exc(anthropic.APIStatusError, status_code=400, body="bad")
        ]
    )
    provider = make_provider(messages)
    with pytest.raises(LLMError) as exc_info:
        await provider.complete("hi")
    assert exc_info.value.reason == LLMErrorReason.PROVIDER_ERROR
    assert messages.create_calls == 1


@pytest.mark.asyncio
async def test_complete_timeout() -> None:
    messages = _FakeMessages(create_results=[_HANG])
    provider = make_provider(messages, timeout=0.05)
    with pytest.raises(LLMError) as exc_info:
        await provider.complete("hi")
    assert exc_info.value.reason == LLMErrorReason.TIMEOUT


@pytest.mark.asyncio
async def test_complete_empty_content() -> None:
    messages = _FakeMessages(create_results=[SimpleNamespace(content=[])])
    provider = make_provider(messages)
    with pytest.raises(LLMError) as exc_info:
        await provider.complete("hi")
    assert exc_info.value.reason == LLMErrorReason.INVALID_OUTPUT


@pytest.mark.asyncio
async def test_complete_non_text_block() -> None:
    messages = _FakeMessages(
        create_results=[SimpleNamespace(content=[object()])]
    )
    provider = make_provider(messages)
    with pytest.raises(LLMError) as exc_info:
        await provider.complete("hi")
    assert exc_info.value.reason == LLMErrorReason.INVALID_OUTPUT


# ---------------------------------------------------------------------------
# stream_complete — full paths via fake client
# ---------------------------------------------------------------------------


async def _collect(provider: AnthropicProvider, msg: str = "hi") -> list[str]:
    return [chunk async for chunk in provider.stream_complete(msg)]


@pytest.mark.asyncio
async def test_stream_happy_path() -> None:
    messages = _FakeMessages(
        stream_results=[_FakeStream(chunks=["a", "b", "c"])]
    )
    provider = make_provider(messages)
    assert await _collect(provider) == ["a", "b", "c"]
    assert messages.stream_calls == 1


@pytest.mark.asyncio
@pytest.mark.usefixtures("no_backoff")
async def test_stream_retries_before_first_chunk() -> None:
    messages = _FakeMessages(
        stream_results=[
            _FakeStream(
                enter_exc=_make_exc(anthropic.APIStatusError, status_code=500)
            ),
            _FakeStream(chunks=["ok"]),
        ]
    )
    provider = make_provider(messages)
    assert await _collect(provider) == ["ok"]
    assert messages.stream_calls == 2


@pytest.mark.asyncio
@pytest.mark.usefixtures("no_backoff")
async def test_stream_retry_exhausted() -> None:
    messages = _FakeMessages(
        stream_results=[
            _FakeStream(enter_exc=_make_exc(anthropic.RateLimitError))
            for _ in range(3)
        ]
    )
    provider = make_provider(messages)
    with pytest.raises(LLMError) as exc_info:
        await _collect(provider)
    assert exc_info.value.reason == LLMErrorReason.RATE_LIMIT
    assert messages.stream_calls == 3


@pytest.mark.asyncio
async def test_stream_auth_error_no_retry() -> None:
    messages = _FakeMessages(
        stream_results=[
            _FakeStream(enter_exc=_make_exc(anthropic.AuthenticationError))
        ]
    )
    provider = make_provider(messages)
    with pytest.raises(LLMError) as exc_info:
        await _collect(provider)
    assert exc_info.value.reason == LLMErrorReason.NO_KEY
    assert messages.stream_calls == 1


@pytest.mark.asyncio
async def test_stream_no_retry_after_first_chunk() -> None:
    """A mid-stream failure maps to LLMError and is never retried."""
    messages = _FakeMessages(
        stream_results=[
            _FakeStream(
                chunks=["partial"],
                exc=_make_exc(anthropic.APIStatusError, status_code=500),
            ),
            _FakeStream(chunks=["should never be reached"]),
        ]
    )
    provider = make_provider(messages)
    received: list[str] = []
    with pytest.raises(LLMError) as exc_info:
        async for chunk in provider.stream_complete("hi"):
            received.append(chunk)
    assert received == ["partial"]
    assert exc_info.value.reason == LLMErrorReason.PROVIDER_ERROR
    assert messages.stream_calls == 1


@pytest.mark.asyncio
async def test_stream_timeout_after_first_chunk() -> None:
    messages = _FakeMessages(
        stream_results=[
            _FakeStream(chunks=["partial"], exc=TimeoutError("read"))
        ]
    )
    provider = make_provider(messages)
    with pytest.raises(LLMError) as exc_info:
        await _collect(provider)
    assert exc_info.value.reason == LLMErrorReason.TIMEOUT
    assert messages.stream_calls == 1
