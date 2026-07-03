# Relay — AI Team Assistant

> A mini-dashboard for dev teams: ask a question — get a structured answer from Claude with a typewriter effect, a history of the last 5 queries, and a "Copy" button.

---

## Screenshot / Demo

Frontend: beige background, black cards, green accent, large Inter typography.
The streamed answer is rendered incrementally — raw JSON is never shown to the user.

---

## Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI (async) + Pydantic v2 + pydantic-settings |
| LLM | Anthropic SDK (Claude `claude-haiku-4-5`), prompt caching, SSE streaming |
| Frontend | React 18 CDN + marked.js + highlight.js + DOMPurify — **a single** `static/index.html`, no bundler |
| Dev | uv, ruff, mypy --strict, pytest (59 tests) |

---

## Quick start

### 1. Clone and install dependencies

```bash
git clone https://github.com/FrostWillmott/Relay.git
cd Relay
uv sync
```

### 2. Create `.env` with your key

```bash
cp .env.example .env
# Open .env and paste your ANTHROPIC_API_KEY
```

### 3. Run the server

```bash
uv run uvicorn main:app --host 127.0.0.1 --port 8000 --env-file .env
```

Open [http://localhost:8000](http://localhost:8000) in your browser.

---

## Architecture (3 layers)

```
routers/      — HTTP only: parsing, calling the service, mapping errors
services/     — business logic: sanitization, LLM orchestration, output validation
providers/    — Protocol wrapper over AsyncAnthropic (native async, no threads)
```

Streaming path `/ask/stream`:
- `AsyncAnthropic.messages.stream()` — the SDK's native async streaming, no `queue.Queue` or `run_in_executor`
- `_extract_answer_from_stream` — an async-generator transformer in the service layer that extracts the `answer` field from the raw JSON stream
- `stream_complete` — an async generator with a retry loop (3 attempts, `2^n` back-off for 429/5xx)
- `ask_stream_llm` — service layer: sanitize → build_message → stream → extract → history

More details: [`TECHNICAL_DECISIONS.md`](TECHNICAL_DECISIONS.md) — 18 architectural decisions with trade-offs.

---

## Quality checks

```bash
# Run all checks with one command
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict app/ main.py && uv run pytest tests/ -v
```

Expected result: `ruff` — 0 errors, `mypy` — 0 errors across 17 files, `pytest` — 59/59 tests passing.

---

## Key features

- **Specialized prompt**: a dev-team assistant, concise structured answers, JSON output schema, user input isolated in `<USER_INPUT>` with explicit system-prompt precedence
- **Prompt caching**: `cache_control: ephemeral` on the system prompt — lowers the cost of repeated calls
- **SSE streaming**: typewriter effect without exposing raw JSON — a state machine decodes only the `answer` field
- **Retry**: 3 attempts with exponential back-off on 429/5xx; no retry on 4xx
- **Output validation**: Pydantic `LLMOutput` + a repair loop on `/ask`; a state machine on `/ask/stream`
- **Prompt injection mitigation**: neutralization (not removal) of injection markers + `<USER_INPUT>` delimiter
- **Error states**: 429 → "Too many requests", 503 → "AI not configured", 504 → "Timeout", network → "No network"

---

## Known limitations

- **No authentication or rate-limiting.** Anyone who can see the port can send requests and burn through your API budget. `/history` returns questions and answers without any authorization. This is a deliberate decision for a localhost demo — production would need API keys, OAuth, or a reverse proxy with limits.
- **History is a per-process `deque`.** With 2+ uvicorn/gunicorn workers, each worker sees its own history. A deliberate trade-off for the contest timebox (see TD §8).
- **No database.** The project is built to demonstrate FastAPI + LLM integration. If the goal is a portfolio piece showcasing async SQLAlchemy/Redis/Celery, Relay doesn't fill that role.
