# Technical Decisions — Relay AI Team Assistant

Each section follows: **Decision → Alternatives Considered → Trade-offs → Why We Chose This**.
Only genuinely contested decisions are documented; obvious or mechanical choices
(prompt caching, model selection, test coverage, minor UI fixes) are omitted.

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

**Why we chose this:** The project scope is a single endpoint with one LLM provider. The 3-layer split gives a clear HTTP boundary (`routers`), business logic boundary (`services`), and I/O boundary (`providers`) — enough seams to test each layer in isolation without the ceremony of full CA.

---

## 2. Provider Abstraction: `Protocol` vs. ABC

**Decision:** `LLMProvider` is defined as a `typing.Protocol` (structural subtyping), not an abstract base class.

**Alternatives considered:**
- `abc.ABC` with `@abstractmethod`
- Duck typing with no formal interface

**Trade-offs:**
- ABC requires explicit inheritance, making third-party or mock providers heavier to write.
- Pure duck typing loses IDE type-checking and static analysis guarantees.
- `Protocol` has no runtime enforcement by default (needs `runtime_checkable` for `isinstance` checks).

**Why we chose this:** `Protocol` gives zero-cost structural subtyping — `AnthropicProvider` satisfies `LLMProvider` simply by having the right method signatures, no inheritance required. Swapping to an `OpenAIProvider` or a `MockProvider` in tests requires no base class boilerplate. mypy validates conformance statically (PEP 544).

---

## 3. Native Async Streaming vs. Thread-Pool Bridging

**Decision:** Use the native `AsyncAnthropic` client — `messages.stream()` for SSE and `messages.create()` for `/ask` — with no `queue.Queue`, no `run_in_executor`, no thread-pool bridging. During streaming, an async-generator transformer (`_extract_answer_from_stream`) extracts only the `answer` field so raw JSON is never shown.

**Alternatives considered:**
- Sync `anthropic.Anthropic` wrapped in `asyncio.to_thread` + a `queue.Queue` bridge for streaming.
- Plain-text prompt for `/ask/stream` (no JSON wrapper, but a second prompt variant and lost metadata).
- Incremental JSON parsing on the frontend (moves complexity to JS, hard to test across chunk boundaries).

**Trade-offs:**
- The `queue.Queue` bridge adds ~40 lines of boilerplate (thread creation, put/get, sentinels) and makes retry fragile — exceptions land in the queue instead of propagating.
- The extraction state machine is ~40 lines of logic, brittle only if the model deviates from the expected `{"answer": "…"}` shape.
- Language detection shifts from a model field to a heuristic (Cyrillic presence → `"ru"`), which is 100% reliable for Russian and defaults to `"en"` otherwise.

**Why we chose this:** The native async client is the simplest correct path — a plain `async for text in stream.text_stream`, no threads or queues. Extracting the `answer` field server-side keeps the frontend dumb and fixes the core UX defect (a typewriter that displays raw JSON). Retry on this path is covered in §4.

---

## 4. Retry Strategy: Manual Loop vs. `tenacity`

**Decision:** Explicit `for attempt in range(3)` loops with `asyncio.sleep(2**attempt)` back-off, shared between `complete()` and `stream_complete()` via a `_map_exc` helper. On the streaming path, retry happens **only before the first chunk is yielded**.

**Alternatives considered:**
- `tenacity` with `retry_if_exception`, `wait_exponential`, `stop_after_attempt`.
- No retry (fail fast and let the client retry).

**Trade-offs:**
- The manual loop is ~10 lines but fully transparent — retry logic is visible at the call site.
- `tenacity` does not support async generators (`async def` + `yield`), which is the signature of `stream_complete`.
- Re-yielding the JSON wrapper from a fresh attempt after chunks already reached the caller would corrupt the caller's extraction state, so mid-stream failures are mapped to `LLMError` instead of retried.

**Why we chose this:** The manual loop is the only approach that works for both a coroutine and an async generator with the same pattern. The back-off (1 s, 2 s) is simple and sufficient for transient 429/5xx errors, and it keeps the dependency tree minimal.

---

## 5. LLM Output Validation: Pydantic `LLMOutput` + Repair Loop

**Decision:** Require the LLM to return a JSON object `{answer}`, validate with a Pydantic model, and attempt one automatic repair call on parse failure.

**Alternatives considered:**
- Plain text extraction (return the raw string, no schema).
- Regex extraction of `{...}` from freeform text.
- Strict JSON-only with no repair (fail loudly on first invalid response).

**Trade-offs:**
- Plain text: no structured metadata, brittle to downstream changes.
- Regex extraction: fragile on nested JSON; false positives on code blocks.
- Strict no-repair: clean but LLMs occasionally wrap JSON in markdown fences despite instructions.

**Why we chose this:** Structured output + Pydantic gives typed, validated data from the first call. The single repair loop handles the most common failure mode (model adds `` ```json `` fences) without open-ended retry. A second failure raises `LLMError("invalid_output")` → 502, which is correct — the issue is the prompt, not the network.

---

## 6. Prompt Injection Mitigation

**Decision:** Sanitize user input (neutralize injection markers without deleting them) + wrap in `<USER_INPUT>` delimiters + append the real instruction *after* the user block + state system-prompt precedence.

**Alternatives considered:**
- Delete injection keywords (filtering): causes data loss, can break legitimate questions about AI/prompts.
- Blocklist approach without delimiters: model still "sees" the text as part of the instruction flow.
- No mitigation: accepts the security risk for a demo context.

**Trade-offs:**
- Neutralization wraps `"ignore previous instructions"` as `"[quoted: ignore previous instructions]"` — the original stays readable as inert, quoted content rather than being destroyed.
- The `<USER_INPUT>…</USER_INPUT>` delimiter pattern is not universally effective against all jailbreaks, but it significantly raises the bar.
- The "real instruction after user block" trick exploits LLM recency bias: the model reads the user block as data, then reads the actual command.

**Why we chose this:** Defence-in-depth without brittleness. Each layer catches different attack vectors. The approach is consistent with Anthropic's guidance on prompt injection in multi-turn systems. Documented in `app/prompts.py`.

---

## 7. History Storage: In-Memory `deque` vs. SQLite / Redis

**Decision:** Store last-5 queries in a process-scoped `collections.deque(maxlen=5)`.

**Alternatives considered:**
- SQLite (via `aiosqlite` or SQLAlchemy): persistent across restarts.
- Redis: persistent + multi-process safe.

**Trade-offs:**
- `deque` data is lost on process restart. In a multi-worker deployment, each worker has its own deque — history is not shared.
- SQLite adds a migration story, file I/O, and a dependency.
- Redis adds a service dependency, connection management, and serialization overhead.

**Why we chose this:** The demo runs as a single-process Uvicorn server; history loss on restart is acceptable and the brief does not require persistence. The service-layer abstraction is designed so that swapping `deque` for a DB-backed store is a one-file change.

---

## 8. Frontend Stack: React CDN + Babel + marked.js vs. Alternatives

**Decision:** Single `index.html` with React 18 UMD via CDN, Babel standalone for JSX, marked.js for Markdown, highlight.js for code, DOMPurify for XSS prevention.

**Alternatives considered:**
- Pure vanilla JS (no framework): feasible but DOM manipulation for Markdown + state becomes messy fast.
- Vue 3 via CDN: similar approach, but React has more ecosystem knowledge for AI demos.
- Next.js / Vite + React: requires a build step, Node.js, npm — violates the "zero build tools" constraint.

**Trade-offs:**
- Babel standalone adds ~1.5 MB to the page and a ~200 ms transpile delay on first load. Acceptable for a demo on localhost; not for production.
- DOMPurify is an additional request but non-negotiable for XSS safety when inserting `marked.parse()` output into the DOM.

**Why we chose this:** React gives clean component-level state without boilerplate; `marked.js` is the most battle-tested 2 KB Markdown library. No build step means the file is served directly from FastAPI's `StaticFiles` mount — the entire frontend is one reviewable file.

---

## Reconsidered Decisions

### Model ID date suffix (`claude-haiku-4-5-20251001` → `claude-haiku-4-5`)

The initial implementation used a date-suffixed model ID. The Anthropic SDK best-practice rule specifies exact model IDs without date suffixes — date variants may not exist in all API regions and are not guaranteed stable aliases. Corrected to `claude-haiku-4-5`.

### `max_tokens`: 1024 → 4096

The initial value of `1024` is appropriate for classification tasks. A developer assistant answering technical questions with code examples can easily produce 800–2000 tokens of structured Markdown. Raised to `4096` — the practical upper bound for a single structured answer from Haiku.

### Prompt language: Russian → English (2026-10-01)

The system, user-wrapper, and repair prompts in `app/prompts.py` were originally in Russian. They are now in English, so a client reading the code doesn't hit untranslated text. Answer language is unaffected: the prompt explicitly tells the model to reply in the language of the question, and `language` is still detected heuristically from the answer (§3).
