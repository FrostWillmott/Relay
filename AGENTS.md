# AGENTS.md — AI assistant dashboard (contest project)

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

## Current project state (updated 2026-06-24)

### What's done (fully verified)
- **Backend Phase 1**: all 3 layers complete (`routers/`, `services/`, `providers/`)
- **Model fix**: `claude-haiku-4-5` (no date suffix) in `app/config.py`
- **Prompt caching**: `cache_control: {type: "ephemeral"}` on system prompt
- **max_tokens**: raised to 4096 (was 1024)
- **Frontend SPA**: `static/index.html` — React 18 CDN + marked.js + highlight.js + DOMPurify, beige/black/green design
- **SSE streaming**: `POST /ask/stream` endpoint, typewriter effect in UI with blinking cursor
- **Streaming fix**: `_sync_stream` now extracts only the `answer` field content via a state machine + JSON-escape decoder — no raw JSON visible during typewriter effect
- **Streaming reliability**: retry loop in `stream_complete` (3 attempts, `2^n` back-off) — `@retry` on `_sync_stream` was a no-op (exceptions go into the queue, not raised to tenacity); per-chunk `asyncio.wait_for` timeout still applies
- **Layer fix**: `ask_stream_llm()` added to `llm_service` — sanitize, build_message, language detection, history.append moved out of router; `/ask/stream` now delegates to the service (mirrors `/ask` pattern)
- **Config fix**: `sanitize()` now uses `settings.max_input_len` instead of hardcoded `_MAX_INPUT_LEN = 2000`; env override `MAX_INPUT_LEN` is now effective
- **Tests**: `tests/test_core.py` — 21 pytest tests: sanitize (5), parse_output (4), ask_llm (3), `_decode_json_char` (5), `ask_stream_llm` (4) — both `/ask` and `/ask/stream` paths covered
- **mypy --strict**: 0 errors on 16 source files (mypy added as dev dep via uv)
- **ruff**: 0 errors, all files formatted
- **TECHNICAL_DECISIONS.md**: 18 architectural decisions at project root
- **UX/UI Improvements**: SVG favicon added; icon 404s (favicon.ico, apple-touch-icons) handled with 204 No Content in `main.py`
- **History persistence**: `HistoryItem` now includes `timestamp` (populated on backend), ensuring timestamps remain visible after page refresh
- **CDN Fix**: Switched to explicit UMD/browser builds for all frontend CDNs and optimized Babel configuration (`data-type="module"`) to resolve `SyntaxError: import declarations may only appear at top level of a module`.
- **Markdown renderer crash fix**: CDN serves `marked` 11.1.1 but the `code` renderer used the v12 object API (`code({text, lang})`), so `text` was `undefined` → `hljs.highlight(undefined,…)` threw `can't access property "replace"`, crashing React into a blank beige screen on any answer with a code block. Renderer now handles both v11 (positional) and v12 (token-object) signatures; `renderMd` wrapped in `try/catch` so a render error can never blank the SPA.

### Streaming architecture note
`AnthropicProvider.stream_complete()` is an `async def` with `yield` (async generator) that uses a `queue.Queue` to bridge the sync `messages.stream()` thread with the async context. The Protocol stub also uses `async def` + `yield` to satisfy mypy's type checking without requiring `await` at the call site.

`_sync_stream` runs a two-state machine: (1) **waiting** — accumulate raw tokens until `"answer": "` regex match; (2) **streaming** — decode JSON escape sequences char by char, emit markdown to the queue, stop at the unescaped closing `"`. Language is detected heuristically in `ask_stream_llm` (Cyrillic presence → `"ru"`). The SSE router (`/ask/stream`) delegates entirely to `ask_stream_llm()` in the service layer — no business logic in the router.

### Key dev commands
```bash
# Lint + type check + tests (run after every change)
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict app/ main.py && uv run pytest tests/ -v

# Format
uv run ruff format .

# Run server with .env
uv run uvicorn main:app --host 127.0.0.1 --port 8000 --env-file .env
```
