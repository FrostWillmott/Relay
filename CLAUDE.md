# CLAUDE.md — AI assistant dashboard (contest project)

## Context
2-hour timeboxed contest task. Build a mini-dashboard: user asks a question,
it goes to an LLM, the answer is rendered nicely, last 5 queries are kept,
answer can be copied. Judged on: working flow, UI/UX, clean code, presentation,
creativity, and a bonus for a real LLM with a tuned prompt (not a bare API call).

I'm a Python backend developer. Stack is therefore **FastAPI backend +
deliberately minimal frontend** (single static page, vanilla JS or React via
CDN). The Python side is where code quality is judged — keep it clean. The
frontend just needs to look good on screen, not in source.

## Rule modules
General Python, LLM, testing, and architecture conventions live in
`.claude/*.md` modules — this file only states project-specific choices and
overrides. When this file conflicts with a module's [PREFER] rule, this file
wins; [MUST] conflicts are surfaced rather than silently resolved.

Documented deviations from modules:
- No `DECISIONS.md` — the decisions log lives in `TECHNICAL_DECISIONS.md`
  (ADR format), per `documentation.md`'s escape hatch.
- `tests/` is flat, not source-mirrored (`testing.md` structure rule) — the
  app is 17 source files; three test modules map cleanly onto the layers.

## Lightweight mode (overrides `clean-architecture.md`)
This project intentionally uses the lighter 3-layer split, NOT full Clean
Architecture. Skipped on purpose for the timebox: full CA, data-engineering
rules (no pipelines here), circuit breakers / Redis idempotency / eval sets
(there's no external-API loop — it's one call per button press).

## Structure (3-layer, lightweight)
- `routers/` — HTTP only: parse request, call a service, shape the response.
  No business logic.
- `services/` — business logic, LLM orchestration, prompt building, validation.
  No knowledge of HTTP. Raises domain exceptions; routers map them to HTTP.
- `providers/` — Protocol wrapper over AsyncAnthropic, selected by a factory.
- Separate Pydantic models for request / response — don't reuse one model.

## Verification scaffolding
- `ruff.toml` in root. Per-file-ignore for Cyrillic prompt files
  (RUF001/002/003) — keep prompts under `prompts.py` or `prompts/`.
- Pre-commit: ruff (lint+format) + mypy strict. Run `pre-commit install` once.
- One command verifies everything; run after each change, fix until green.
- Verify hook versions with `pre-commit autoupdate` — don't trust pinned revs.

## Russian/non-ASCII note
Prompt strings are in Russian. The ruff per-file-ignore above handles the
"ambiguous character" rules locally — don't disable RUF globally to work around
it.

## Streaming
The app exposes two endpoints:
- `POST /ask` — synchronous, returns `{answer, language}` JSON.
- `POST /ask/stream` — **SSE**, streams raw JSON chunks `{chunk: "..."}` then
  a final `{done: true, answer, language}`. Use this for the UI typewriter effect.

`AnthropicProvider.stream_complete()` uses the native `AsyncAnthropic.messages.stream()` — no `queue.Queue`, no `run_in_executor`, no thread-pool bridging. It yields raw text chunks via `stream.text_stream` and retries on transient failures (429/5xx) **before the first chunk**; once streaming has started, errors are mapped to `LLMError` rather than retried.

`_extract_answer_from_stream` — an async-generator transformer in the service layer — runs a two-state machine: (1) **waiting** — accumulate raw tokens until `"answer": "` regex match; (2) **streaming** — decode JSON escape sequences char by char, emit clean markdown, stop at the unescaped closing `"`. Language is detected heuristically (Cyrillic presence → `"ru"`). The SSE router delegates entirely to `ask_stream_llm()` — no business logic in the router.

The frontend uses `fetch` + `ReadableStream` (no `EventSource`) — this lets us
POST a JSON body and read SSE lines manually via `getReader()`.

## Current state (updated 2026-07-24)
- **All 3 layers**: routers, services, providers — complete, 17 source files, mypy --strict clean.
- **Model**: `claude-haiku-4-5` in `app/config.py`, prompt caching enabled, `max_tokens=4096`.
- **Frontend**: `static/index.html` — React 18 CDN + marked.js + highlight.js + DOMPurify, single file, no bundler.
- **Tests**: 82 pytest tests, coverage 97%. Provider is tested through a fake
  SDK client (`tests/test_providers.py`) incl. retry/timeout/mid-stream paths;
  `test_ask_stream_integration_fake_sdk` covers fake SDK stream → SSE end to end.
- **Docs**: `TECHNICAL_DECISIONS.md` (18 ADRs), `README.md`.
- **Known limitations**: no auth/rate-limiting, history is per-process `deque` (not shared across workers), no database. See `README.md` and TD §8 for rationale.

## Key dev commands
```bash
# Full check — run after every change, fix until green
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict app/ main.py && uv run pytest tests/ -v

# Auto-fix formatting
uv run ruff format .

# Launch server (reads ANTHROPIC_API_KEY from .env)
uv run uvicorn main:app --host 127.0.0.1 --port 8000 --env-file .env
```
