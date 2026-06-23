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

## Lightweight mode (deliberate, not an oversight)
This project intentionally uses the lighter 3-layer split, NOT full Clean
Architecture. Per the rule levels, this project's CLAUDE.md takes precedence
over [PREFER] rules — don't impose the full domain/use-case/adapter split here.
Skipped on purpose for the timebox: full Clean Architecture, data-engineering
rules (no pipelines here), circuit breakers / Redis idempotency / eval sets
(there's no external-API loop — it's one call per button press).

## Structure (3-layer, lightweight)
- `routers/` — HTTP only: parse request, call a service, shape the response.
  No business logic.
- `services/` — business logic, LLM orchestration, prompt building, validation.
  No knowledge of HTTP. Raises domain exceptions; routers map them to HTTP.
- Config + provider client live behind an interface.
- Separate Pydantic models for request / response — don't reuse one model.

## LLM rules (these earn the +10 and avoid the penalties) [MUST]
- **Specialized prompt, not a bare passthrough.** A system prompt scopes the
  assistant as a concise team helper (short, structured, on-point answers).
  This is the explicit difference between "real LLM" and "ChatGPT clone" the
  brief penalizes.
- **User input is untrusted.** Sanitize before it enters the prompt: neutralize
  (don't delete) injection markers ("ignore previous", "SYSTEM:", fenced
  markers). Isolate user text in explicit delimiters, tell the model to treat
  everything inside as data, and put the real instructions after it with stated
  precedence. Truncate to a sane max length.
- **Validate model output** against a schema before use; handle the parse-fail
  path explicitly (retry/repair/fail loudly). Treat output as untrusted too.
- **Secrets from env only.** `ANTHROPIC_API_KEY` read from env/settings, never
  hardcoded, never logged.
- **Don't block the event loop.** The Anthropic SDK is sync — wrap calls in
  `asyncio.to_thread`, don't fake-async it.
- **Error handling, always.** No crash on: empty input, missing key, network
  failure, timeout, rate limit (429). Explicit loading / error / success states.
  Timeout on the call; retry transient errors once or twice with backoff; do NOT
  retry 4xx validation errors.

## Provider abstraction [PREFER]
Model provider behind a `Protocol`/ABC, selected by a small factory, so a
provider swap doesn't ripple. Config selects provider via a `Literal`.

## Python conventions [MUST / MUST-UNLESS]
- Type hints on every signature incl. return. Modern syntax (`X | None`,
  `list[str]`). `from __future__ import annotations` at top of each module.
- mypy --strict is part of done. Local `# type: ignore[code]  # reason` only,
  never a global disable.
- No bare `except:` / `except Exception:` — catch specific exceptions. Named
  domain exceptions per layer. Don't swallow silently.
- No mutable default args. `pathlib`, not `os.path`. `logging`, not `print`.
  f-strings. Composition over inheritance; `@dataclass`/Pydantic for DTOs.
- `uv` for deps/env; pin in the lockfile. Absolute imports.
- Docstrings: yes (ruff `D` is on, google convention). AI writes them.

## Verification scaffolding
- `ruff.toml` in root (already present). Note the per-file-ignore for Cyrillic
  prompt files (RUF001/002/003) — keep prompts under a path the glob matches
  (`prompts.py` or `prompts/`).
- Pre-commit: ruff (lint+format) + mypy strict, all enabled. Run
  `pre-commit install` once myself.
- One command verifies everything; run it after each change, fix until green.
- Versions: verify current tags with `pre-commit autoupdate` rather than
  trusting the pinned revs from memory — ruff rule codes drift between versions.

## Russian/non-ASCII note
Prompt strings are in Russian. The ruff per-file-ignore above handles the
"ambiguous character" rules locally — don't disable RUF globally to work around
it.

## Streaming (added in Phase 2)
The app exposes two endpoints:
- `POST /ask` — synchronous, returns `{answer, language}` JSON.
- `POST /ask/stream` — **SSE**, streams raw JSON chunks `{chunk: "..."}` then
  a final `{done: true, answer, language}`. Use this for the UI typewriter effect.

`AnthropicProvider.stream_complete()` is an `async def` generator (uses `yield`
internally) that bridges `client.messages.stream()` (sync, runs in a thread-pool
via `run_in_executor`) and the async context via `queue.Queue`. The Protocol
stub mirrors this with `async def` + `yield` so `mypy --strict` accepts it
without `await` at call sites.

The frontend uses `fetch` + `ReadableStream` (no `EventSource`) — this lets us
POST a JSON body and read SSE lines manually via `getReader()`.

## Key dev commands (for AI assistants)
```bash
# Full check — run after every change, fix until green
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict app/ main.py

# Auto-fix formatting
uv run ruff format .

# Launch server (reads ANTHROPIC_API_KEY from .env)
uv run uvicorn main:app --host 127.0.0.1 --port 8000 --env-file .env
```
