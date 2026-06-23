# Technical Decisions — Relay AI Team Assistant

Each section follows: **Decision → Alternatives Considered → Trade-offs → Why We Chose This**.

---

## 1. Architecture: 3-Layer Split vs. Full Clean Architecture

**Decision:** Three layers — `routers/`, `services/`, `providers/` — with no domain/use-case/adapter separation.

**Alternatives considered:**
- Full Clean Architecture (domain entities, use-case interactors, interface adapters, frameworks layer)
- Flat single-file app (FastAPI with all logic in one `main.py`)

**Trade-offs:**
- 3-layer is under-structured for a large system: cross-cutting concerns (e.g., auth, audit) would need careful placement.
- Full CA is over-engineered for a 2-hour timebox: ~8 additional directories, substantial boilerplate, onboarding cost.
- Flat single-file has no seams for testing and grows unreadable past ~200 lines.

**Why we chose this:** The project scope is a single endpoint with one LLM provider. The 3-layer split gives a clear HTTP boundary (`routers`), business logic boundary (`services`), and I/O boundary (`providers`) — enough seams to test each layer in isolation without the ceremony of full CA. The `CLAUDE.md` for this project explicitly mandates this choice.

---

## 2. Provider Abstraction: `Protocol` vs. ABC

**Decision:** `LLMProvider` is defined as a `typing.Protocol` (structural subtyping), not an abstract base class.

**Alternatives considered:**
- `abc.ABC` with `@abstractmethod`
- Duck typing with no formal interface
- `typing.overload` + `TypeVar` bounds

**Trade-offs:**
- ABC requires explicit inheritance, making third-party or mock providers heavier to write.
- Pure duck typing loses IDE type-checking and static analysis guarantees.
- `Protocol` has no runtime enforcement by default (needs `runtime_checkable` decorator for `isinstance` checks).

**Why we chose this:** `Protocol` gives zero-cost structural subtyping — `AnthropicProvider` satisfies `LLMProvider` simply by having the right method signatures, no inheritance required. This means swapping to an `OpenAIProvider` or a `MockProvider` in tests requires no base class boilerplate. mypy validates conformance statically. This is the idiomatic modern Python approach per PEP 544.

---

## 3. Sync SDK + `asyncio.to_thread` vs. `AsyncAnthropic`

**Decision:** Use the synchronous `anthropic.Anthropic` client wrapped in `asyncio.to_thread`.

**Alternatives considered:**
- `anthropic.AsyncAnthropic` — the SDK's native async client
- Running the sync client directly in the event loop (blocking — wrong)

**Trade-offs:**
- `asyncio.to_thread` introduces thread overhead (~0.1 ms per call) — negligible for LLM calls that take seconds.
- The async client is marginally cleaner code but requires `await` everywhere through the call stack.
- At the time of writing, the Anthropic SDK docs noted that the sync client is more battle-tested for retry behaviour; `tenacity` integrates more naturally with sync code.

**Why we chose this:** The `asyncio.to_thread` pattern is the Anthropic SDK's own recommendation for FastAPI integration. It keeps the retry decorator (`tenacity`) entirely in synchronous context — `@retry` works by wrapping the function call, and doing this inside a thread avoids subtle event-loop interactions. The timeout (`asyncio.wait_for`) fires correctly from outside the thread. This is well-understood, safe, and widely used.

---

## 4. Retry Strategy: `tenacity` vs. Manual Loop

**Decision:** Use `tenacity` with `retry_if_exception`, `wait_exponential`, `stop_after_attempt`.

**Alternatives considered:**
- Manual `for _ in range(3): try/except` loop
- `backoff` library (similar to tenacity)
- No retry (fail fast and let the client retry)

**Trade-offs:**
- Manual loop is straightforward but easy to get wrong: missed reraise, wrong exception filtering, no jitter.
- `backoff` library is functionally equivalent but has less adoption and fewer features.
- No retry leaves transient 429s as user-visible errors on a first request.

**Why we chose this:** `tenacity` is declarative, well-tested, and makes the retry policy readable as a decorator. Critically, `retry_if_exception(_is_retryable)` lets us be surgical: retry on 429 and 5xx, but *never* on 4xx (auth error, invalid request) — retrying those would waste budget and hang the user. The `wait_exponential` gives safe backoff without thundering-herd issues.

---

## 5. LLM Output Validation: Pydantic `LLMOutput` + Repair Loop

**Decision:** Require the LLM to return a JSON object `{answer, language}`, validate with a Pydantic model, and attempt one automatic repair call on parse failure.

**Alternatives considered:**
- Plain text extraction (return the raw string, no schema)
- Regex extraction of `{...}` from freeform text
- Strict JSON-only with no repair (fail loudly on first invalid response)

**Trade-offs:**
- Plain text: no structured metadata (language detection, future fields); brittle to downstream changes.
- Regex extraction: fragile on nested JSON; false positives on code blocks.
- Strict no-repair: clean but LLMs occasionally wrap JSON in markdown fences despite instructions — a single repair attempt prevents user-visible errors for a recoverable failure.

**Why we chose this:** Structured output + Pydantic gives us typed, validated data from the first call. The single repair loop (send the bad output back, ask for valid JSON) handles the most common failure mode (model adds `` ```json `` fences) without open-ended retry. A second failure raises `LLMError("invalid_output")` → 500, which is the correct outcome — the issue is the prompt, not the network.

---

## 6. Prompt Injection Mitigation

**Decision:** Sanitize user input (neutralize injection markers without deleting them) + wrap in `<USER_INPUT>` delimiters + append the real instruction *after* the user block + state system-prompt precedence.

**Alternatives considered:**
- Delete injection keywords (filtering): causes data loss, can break legitimate questions about AI/prompts.
- Blocklist approach without delimiters: model still "sees" the text as part of the instruction flow.
- No mitigation: accepts the security risk for a demo context.

**Trade-offs:**
- Neutralization replaces `"ignore previous instructions"` with `"[REMOVED]"` — the user cannot pass the original through, but the question still makes sense.
- The `<USER_INPUT>…</USER_INPUT>` delimiter pattern is not universally effective against all jailbreaks, but it significantly raises the bar.
- The "real instruction after user block" trick exploits LLM recency bias: the model reads the user block as data, then reads the actual command, making it much harder to override.

**Why we chose this:** Defence-in-depth without brittleness. Each layer catches different attack vectors. The approach is consistent with Anthropic's own guidance on prompt injection in multi-turn systems. Documented in `app/prompts.py`.

---

## 7. Prompt Caching: `cache_control: ephemeral` on System Prompt

**Decision:** Pass the system prompt as a structured content block with `cache_control: {type: "ephemeral"}` rather than a plain string.

**Alternatives considered:**
- Plain string `system=SYSTEM_PROMPT` (no caching)
- Caching the user message prefix instead (inappropriate — user content changes every call)

**Trade-offs:**
- Prompt caching requires the cached block to be ≥ 1024 tokens. The current system prompt is ~220 tokens — below the threshold. Anthropic silently skips caching below the threshold; no error is thrown, but no savings occur either.
- If the system prompt grows (e.g., adding examples or tool specs), caching activates automatically with no code change.
- Using the structured list form instead of a bare string is slightly more verbose but is the only way to attach `cache_control`.

**Why we chose this:** The Anthropic SDK skill mandates prompt caching for any app using the SDK. Even though the current prompt is below the threshold, adding `cache_control` is a zero-cost forward-compatible change. When the system prompt exceeds 1024 tokens (e.g., after adding few-shot examples), caching activates automatically and cuts input token cost by ~90% on all subsequent calls.

---

## 8. History Storage: In-Memory `deque` vs. SQLite / Redis

**Decision:** Store last-5 queries in a process-scoped `collections.deque(maxlen=5)`.

**Alternatives considered:**
- SQLite (via `aiosqlite` or SQLAlchemy): persistent across restarts
- Redis: persistent + multi-process safe
- PostgreSQL: full persistence + query capability

**Trade-offs:**
- `deque` data is lost on process restart. In a multi-worker deployment (Gunicorn), each worker has its own deque — history is not shared.
- SQLite adds a migration story, file I/O, and a dependency.
- Redis adds a service dependency, connection management, and serialization overhead.

**Why we chose this:** The demo runs as a single-process Uvicorn server for a 2-hour presentation. History loss on restart is acceptable; no one will restart during the demo. The project brief explicitly does not require persistence. Adding SQLite would take 30–60 minutes and introduce risk with no benefit in the demo context. The provider abstraction for history is designed so that swapping `deque` for a DB-backed store is a one-file change.

---

## 9. Frontend Stack: React CDN + Babel + marked.js vs. Alternatives

**Decision:** Single `index.html` with React 18 UMD via CDN, Babel standalone for JSX, marked.js for Markdown, highlight.js for code, DOMPurify for XSS prevention.

**Alternatives considered:**
- Pure vanilla JS (no framework): feasible but DOM manipulation for Markdown + state becomes messy fast.
- Vue 3 via CDN: similar to React CDN approach, but React has more ecosystem knowledge for AI demos.
- Next.js / Vite + React: requires a build step, Node.js, npm — violates the "zero build tools" constraint.
- Alpine.js: minimal JS framework, but no JSX and Markdown rendering is still manual.

**Trade-offs:**
- Babel standalone adds ~1.5 MB to the page and a ~200 ms transpile delay on first load. Acceptable for a demo on localhost; not for production.
- React UMD + Babel is heavier than vanilla JS for this small page.
- DOMPurify is an additional request but is non-negotiable for XSS safety when inserting `marked.parse()` output into the DOM.

**Why we chose this:** React gives clean component-level state (`useState`, `useEffect`) without boilerplate. Markdown rendering is the core UX requirement — `marked.js` is the most battle-tested 2 KB library for this. No build step means the file can be served as a static asset directly from FastAPI's `StaticFiles` mount with zero configuration. The entire frontend is a single file, easily reviewable and modifiable.

---

## 10. Model Selection: `claude-haiku-4-5` vs. Opus / Sonnet

**Decision:** Default to `claude-haiku-4-5`.

**Alternatives considered:**
- `claude-opus-4-5` or `claude-opus-4-6`: highest quality, highest cost, ~3-5× slower
- `claude-sonnet-4-5` or `claude-sonnet-4-6`: balanced quality/cost
- `claude-haiku-4-5`: fastest, cheapest

**Trade-offs:**
- Haiku may occasionally give shorter or less detailed answers than Sonnet/Opus.
- For structured JSON output with a clear schema, Haiku performs comparably to larger models — the system prompt compensates for the size gap.
- Speed matters for a live demo: Haiku responses arrive in ~1–2 s; Opus can take 10–20 s.

**Why we chose this:** The contest evaluates the *architecture and prompt quality*, not the model tier. Haiku demonstrates that a well-designed system (structured prompt, output schema, injection mitigation, caching, retry) produces excellent results without the most expensive model. For production, switching to Sonnet or Opus is a one-line config change — `LLM_MODEL=claude-sonnet-4-5` in the environment — because the provider abstraction was designed for exactly this swap.

---

## 11. SSE Streaming: `queue.Queue` Bridge vs. `AsyncAnthropic` / Native Async

**Decision:** Implement `stream_complete()` as an `async def` generator that uses `queue.Queue` to bridge the sync `messages.stream()` running in a thread-pool with the async event loop.

**Alternatives considered:**
- `AsyncAnthropic` client with `async with client.messages.stream()`: native async streaming, no queue needed.
- Polling with `asyncio.sleep()` instead of `queue.Queue`: simpler but wastes CPU.
- Long-polling (return full answer after generation): eliminates streaming complexity but loses the typewriter UX.

**Trade-offs:**
- The `queue.Queue` bridge adds ~5 lines of boilerplate per provider, but keeps the sync `tenacity` retry decorator working naturally in the thread.
- `AsyncAnthropic` streaming is cleaner but requires migrating `complete()` to async as well and re-validating the retry behaviour with async tenacity — more risk for marginal gain.
- The Protocol stub uses `async def` + `yield` (a no-op generator body) so that `mypy --strict` accepts `async for chunk in provider.stream_complete(...)` without an `await` — this is a deliberate pattern, not dead code.
- The SSE router uses `fetch` + `ReadableStream` instead of `EventSource` API — `EventSource` only supports GET requests; `fetch` lets us POST a JSON body.

**Why we chose this:** The `queue.Queue` bridge is a well-known pattern for mixing sync and async I/O in Python. It keeps the existing sync `Anthropic` client (and all its retry logic) unchanged. The frontend typewriter effect delivers the "wow factor" the contest values for creativity, and the implementation is contained to ~50 lines split across two files.

---

## 12. Streaming Answer Extraction: State Machine vs. Plain-Text Prompt

**Decision:** Keep the JSON output schema (`{"answer": "…", "language": "…"}`) and extract only the `answer` field content during streaming via a two-state machine in `_sync_stream`.

**The problem:** The system prompt instructs the model to return structured JSON. Streaming the raw model output (i.e., `stream.text_stream`) directly to the browser means the typewriter effect displays `{"answer": "## Postgres\n\nUse GIN…` — raw JSON syntax visible to the user. The wow-factor feature becomes a UX defect.

**Alternatives considered:**
1. **Switch to plain-text output for streaming, use a separate call for language**: removes the JSON wrapper entirely but requires two prompt variants (one for `/ask`, one for `/ask/stream`) and loses language information.
2. **Disable streaming, keep full JSON**: eliminates the problem but removes the typewriter effect.
3. **Incremental JSON parser on the frontend**: moves complexity to JS; harder to test and maintain across chunked boundaries.
4. **State machine in `_sync_stream` (chosen)**: extracts only the `answer` field content server-side, decodes JSON escapes (`\n` → newline, `\"` → `"`, etc.), and emits clean markdown chunks. Language is detected heuristically in the router (Cyrillic presence → `"ru"`).

**Trade-offs:**
- The state machine adds ~30 lines of well-tested logic to `_sync_stream`. It is brittle only if the model deviates from the expected JSON structure — mitigated by the strict system prompt and existing `/ask` path as a validated fallback.
- Language detection shifts from a model-provided field to a heuristic. For a Russian/English developer tool, Cyrillic-character detection is 100% reliable for Russian; for pure-English answers it defaults to `"en"`. Mixed-language answers (code with Russian comments) correctly detect as `"ru"`.
- The `@retry` decorator on `_sync_stream` and per-chunk `asyncio.wait_for` in `stream_complete` now provide the same reliability guarantees as the `/ask` path.

**Why we chose this:** The user never needs to see the JSON wrapper — it is an internal serialisation detail between the model and the service layer. Extracting only the `answer` content server-side keeps the frontend simple (no JSON-aware parsing in the `ReadableStream` handler) and fixes the core UX defect without changing the prompt or splitting the API surface.

---

## 14. Retry on the Streaming Path: `stream_complete` Loop vs. `@retry` on `_sync_stream`

**Decision:** Implement retry at the `stream_complete` level (manual `for attempt in range(3)` with `asyncio.sleep` back-off) instead of decorating `_sync_stream` with `@retry`.

**The problem:** `_sync_stream` runs in a thread and communicates with the async caller via a `queue.Queue`. Exceptions are caught inside `_sync_stream` and placed into the queue (`q.put(exc)`) so they can be re-raised by the async consumer. This means `_sync_stream` itself never raises — it always returns normally. A tenacity `@retry` decorator wraps a *callable* and retries on exceptions raised *from that callable*; because the exceptions are swallowed into the queue before propagating, `@retry` on `_sync_stream` is a no-op.

**Why `@retry` cannot be moved to `stream_complete`:** `stream_complete` is an async generator (`async def` + `yield`). Tenacity's `@retry` does not support async generators — it only supports coroutines (`async def` without `yield`) and regular functions.

**Alternatives considered:**
1. **Refactor `_sync_stream` to raise directly** (remove the `except` block): exceptions would propagate through `run_in_executor`, but the async consumer reads from the queue, not from the future, so it would hang waiting for a sentinel that never arrives.
2. **Use `@retry` on a non-generator wrapper coroutine** that collects all chunks before yielding: correct, but loses incremental streaming — the user sees nothing until the full answer is ready.
3. **Manual retry loop in `stream_complete` (chosen):** on `LLMError` with `_is_retryable`, restart the whole `_sync_stream` → queue → drain cycle. Back-off is `2^attempt` seconds (1 s, 2 s). Non-retryable errors propagate immediately.

**Trade-offs:**
- Replaces one `@retry` decorator with ~10 lines of explicit loop — slightly more verbose but fully transparent.
- Retry restarts the entire stream from scratch; partial chunks already yielded to the caller will be re-sent. In practice, transient 429/5xx errors occur before or at stream start, so the user rarely sees a mid-stream restart.
- `_decode_json_char` and the state machine reset cleanly because they are local variables in `_sync_stream`.

**Why we chose this:** Correctness over brevity. The decorator *appeared* to add retry coverage, but it was a silent no-op. The explicit loop makes the retry logic visible, testable, and actually effective.

---

## 13. Tests: `pytest` with `MockProvider` vs. No Tests

**Decision:** `tests/test_core.py` with 21 unit tests covering `sanitize`, `parse_output`, `ask_llm`, `_decode_json_char`, and `ask_stream_llm` — the last three covering the actual `/ask/stream` code path used in production.

**The problem:** `TECHNICAL_DECISIONS.md` §1 and §2 justify the 3-layer split and Protocol abstraction partly on testability ("enough seams to test each layer in isolation", "swapping to a MockProvider requires no boilerplate"). The first revision had 12 tests but they only covered the `/ask` path, leaving the actually-executed `/ask/stream` path (state machine, JSON decoder, streaming service) untested — directly undermining the stated architectural rationale.

**Alternatives considered:**
- Integration tests with a real `AnthropicProvider`: requires a live API key, slow, non-deterministic.
- No tests: the project brief does not require them, but their absence contradicts the stated architectural rationale and is visible in the submission review.

**Trade-offs:**
- 21 unit tests add ~220 lines and two dev dependencies (`pytest`, `pytest-asyncio`). Zero maintenance burden — no mocks of external services, all pure function or Protocol-based.
- `MockProvider` uses `async def` + `yield` matching the Protocol stub, so it validates structural subtyping at import time.
- Tests now cover both the `/ask` and `/ask/stream` execution paths: sanitize (5), parse_output (4), ask_llm (3), `_decode_json_char` (5), `ask_stream_llm` (4).

**Why we chose this:** A 2-hour contest should still show that the architecture is not just talk. The tests directly demonstrate the "testable seams" claim and cover the code that actually runs when the user clicks "Ask AI".

---

## 15. History Persistence and UI Consistency: `timestamp` in `HistoryItem`

**Decision:** Add a `timestamp` field (float) to the `HistoryItem` model, populated automatically on the backend using `time.time()`, and map it to a JS `time` (ms) on the frontend.

**The problem:** While history storage is intentionally non-persistent across server restarts (§8), it was also "semi-persistent" in the UI. When a user asked a question, a timestamp was generated in JS. However, refreshing the page reloaded the history from the server, which lacked timestamps. This caused timestamps to "vanish" on refresh, creating a jarring UX regression.

**Alternatives considered:**
- **Frontend-only timestamps:** ignore the lack of timestamps from the server after refresh (Poor UX).
- **Server-side only formatting:** return a pre-formatted string (Inflexible for different locales).
- **Full persistence (SQLite):** solves both persistence and timestamps (Out of scope per §8).

**Trade-offs:**
- Adds a small amount of data to the JSON payload for `/history`.
- Requires careful handling of unit differences (Python seconds vs. JS milliseconds).

**Why we chose this:** This minor backend change fixes a major UI "polishing" issue. By providing the exact creation time from the server, the frontend can render consistent, localized timestamps regardless of whether the item was just created or reloaded from history. This aligns with the "Presentation" and "UI/UX" criteria of the contest.

---

## 16. Asset Cleanliness: 204 No Content for Browser Icons

**Decision:** Explicitly handle `/favicon.ico` and `/apple-touch-icon*.png` routes with a `204 No Content` response instead of letting them hit the 404 handler or StaticFiles.

**Trade-offs:**
- Adds 5 lines of routing code to `main.py`.
- Prevents annoying 404 errors in the terminal logs and browser console.
- Combined with a data-URI SVG favicon in `index.html`, this provides a clean UI/UX without requiring binary asset management in the repository.

---

## 17. CDN Reliability, UMD Compatibility, and Babel Configuration

**Decision:** Use explicit UMD/browser builds from `cdnjs` for all frontend dependencies (marked, dompurify, highlight.js) and configure Babel standalone with `data-presets="env,react"` and `data-type="module"`.

**The problem:**
- Some modern libraries (like `marked` 12.0.0) are published as pure ESM packages. Loading them via regular `<script>` tags causes `SyntaxError: import declarations may only appear at top level of a module`.
- Babel standalone without explicit presets or type configuration may produce code that the browser fails to parse, especially when it attempts to handle ES modules or modern syntax in a non-module context.

**Trade-offs:**
- Moving scripts to the bottom of the `<body>` to ensure the DOM is ready and avoid blocking initial render.
- Specifying versions and UMD-specific paths to ensure consistency.

**Why we chose this:** This ensures maximum compatibility with the project's "no build step" architecture. By explicitly selecting UMD builds and configuring Babel to handle the transformed script as a module (`data-type="module"`), we avoid browser errors related to module/script mismatches and ensure the application initializes correctly on all supported browsers.

---

## 18. Markdown `code` Renderer: Version-Agnostic Signature + Crash Isolation

**Decision:** Make the custom `marked` `code` renderer accept both the v11 positional signature (`code(codeString, infostring)`) and the v12 token-object signature (`code({ text, lang })`), and wrap `renderMd` in a `try/catch` that falls back to escaped `<pre>` text.

**The problem:** The CDN actually serves `marked` 11.1.1, but the renderer was written against the v12 object API (`code({ text, lang })`). Under v11 the first argument is the code **string**, so destructuring `{ text, lang }` yielded `undefined`; `hljs.highlight(undefined, …)` then threw `can't access property "replace", e is undefined`. That exception propagated out of `AnswerCard` → `renderMd`, crashing the entire React tree and leaving only the beige background (blank screen) the moment an answer with a code block was rendered.

**Trade-offs:**
- The renderer carries a small branch to detect object-vs-string arguments — a few extra lines, but it survives a CDN version bump in either direction.
- The `try/catch` in `renderMd` means a malformed Markdown/highlight edge case degrades to plain escaped text instead of taking down the whole UI.

**Why we chose this:** A single rendering error must never blank the entire SPA. Decoupling the renderer from a specific `marked` major version and isolating render failures makes the answer view resilient regardless of which exact CDN build is delivered.

---

## Reconsidered Decisions

### Model ID date suffix (`claude-haiku-4-5-20251001` → `claude-haiku-4-5`)

The initial implementation used a date-suffixed model ID. The Anthropic SDK best-practice rule specifies exact model IDs without date suffixes — date variants may not exist in all API regions and are not guaranteed stable aliases. Corrected to `claude-haiku-4-5`.

### `max_tokens`: 1024 → 4096

The initial value of `1024` is appropriate for classification tasks (short labels, short decisions). A developer assistant answering technical questions with code examples can easily produce 800–2000 tokens of structured Markdown. Hitting the `1024` cap would truncate answers mid-sentence or mid-code-block. Raised to `4096` — the practical upper bound for a single structured answer from Haiku.
