"""HTTP endpoint tests using FastAPI TestClient."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Generator

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.exceptions import LLMError
from app.models.request import AskRequest
from app.services import history as history_service
from main import app


class _MockProvider:
    def __init__(
        self,
        response: str = "",
        chunks: list[str] | None = None,
        complete_error: LLMError | None = None,
        stream_error: LLMError | None = None,
    ) -> None:
        self._response = response
        self._chunks = chunks
        self._complete_error = complete_error
        self._stream_error = stream_error

    async def complete(self, user_message: str) -> str:
        if self._complete_error is not None:
            raise self._complete_error
        return self._response

    async def stream_complete(self, user_message: str) -> AsyncIterator[str]:
        if self._stream_error is not None:
            raise self._stream_error
        items = self._chunks if self._chunks is not None else [self._response]
        for item in items:
            yield item


_VALID_PAYLOAD = json.dumps({"answer": "## Result", "language": "en"})


@pytest.fixture(autouse=True)
def clear_history() -> Generator[None, None, None]:
    history_service._store.clear()  # type: ignore[attr-defined]
    yield
    history_service._store.clear()  # type: ignore[attr-defined]


@pytest.fixture
def client() -> TestClient:
    app.state.provider = _MockProvider(response=_VALID_PAYLOAD)
    return TestClient(app)


# ---------------------------------------------------------------------------
# AskRequest model
# ---------------------------------------------------------------------------


def test_ask_request_valid() -> None:
    req = AskRequest(question="hello")
    assert req.question == "hello"


def test_ask_request_strips_whitespace() -> None:
    req = AskRequest(question="  hello  ")
    assert req.question == "hello"


def test_ask_request_empty_raises() -> None:
    with pytest.raises(ValidationError):
        AskRequest(question="")


def test_ask_request_whitespace_only_raises() -> None:
    with pytest.raises(ValidationError):
        AskRequest(question="   ")


# ---------------------------------------------------------------------------
# POST /ask
# ---------------------------------------------------------------------------


def test_ask_happy_path(client: TestClient) -> None:
    resp = client.post("/ask", json={"question": "Hello?"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["answer"] == "## Result"
    assert data["language"] == "en"


def test_ask_empty_question(client: TestClient) -> None:
    resp = client.post("/ask", json={"question": "   "})
    assert resp.status_code == 422


def test_ask_no_key(client: TestClient) -> None:
    app.state.provider = _MockProvider(complete_error=LLMError("no_key"))
    resp = client.post("/ask", json={"question": "hi"})
    assert resp.status_code == 503


def test_ask_rate_limit(client: TestClient) -> None:
    app.state.provider = _MockProvider(complete_error=LLMError("rate_limit"))
    resp = client.post("/ask", json={"question": "hi"})
    assert resp.status_code == 429


def test_ask_timeout(client: TestClient) -> None:
    app.state.provider = _MockProvider(complete_error=LLMError("timeout"))
    resp = client.post("/ask", json={"question": "hi"})
    assert resp.status_code == 504


def test_ask_invalid_output(client: TestClient) -> None:
    app.state.provider = _MockProvider(
        complete_error=LLMError("invalid_output")
    )
    resp = client.post("/ask", json={"question": "hi"})
    assert resp.status_code == 502


def test_ask_unknown_error_maps_to_502(client: TestClient) -> None:
    app.state.provider = _MockProvider(
        complete_error=LLMError("provider_error")
    )
    resp = client.post("/ask", json={"question": "hi"})
    assert resp.status_code == 502


# ---------------------------------------------------------------------------
# POST /ask/stream
# ---------------------------------------------------------------------------


def _parse_sse(text: str) -> list[dict]:  # type: ignore[type-arg]
    return [
        json.loads(line[5:].strip())
        for line in text.splitlines()
        if line.startswith("data:")
    ]


def test_ask_stream_happy_path(client: TestClient) -> None:
    app.state.provider = _MockProvider(
        chunks=['{"answer": "Hello ', 'world"}']
    )
    resp = client.post("/ask/stream", json={"question": "hi"})
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    chunks = [e["chunk"] for e in events if "chunk" in e]
    done = next((e for e in events if e.get("done")), None)
    assert chunks == ["Hello ", "world"]
    assert done is not None
    assert done["answer"] == "Hello world"


def test_ask_stream_empty_question(client: TestClient) -> None:
    resp = client.post("/ask/stream", json={"question": "  "})
    assert resp.status_code == 422


def test_ask_stream_llm_error(client: TestClient) -> None:
    app.state.provider = _MockProvider(stream_error=LLMError("rate_limit"))
    resp = client.post("/ask/stream", json={"question": "hi"})
    events = _parse_sse(resp.text)
    assert any(e.get("error") == "rate_limit" for e in events)


# ---------------------------------------------------------------------------
# GET /history
# ---------------------------------------------------------------------------


def test_history_empty(client: TestClient) -> None:
    resp = client.get("/history")
    assert resp.status_code == 200
    assert resp.json() == []


def test_history_after_ask(client: TestClient) -> None:
    client.post("/ask", json={"question": "What is 2+2?"})
    resp = client.get("/history")
    hist = resp.json()
    assert len(hist) == 1
    assert hist[0]["question"] == "What is 2+2?"
