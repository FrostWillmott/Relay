# Relay — AI Team Assistant

> Мини-дашборд для команды разработчиков: задаёшь вопрос — получаешь структурированный ответ от Claude с эффектом печатной машинки, историей последних 5 запросов и кнопкой «Скопировать».

---

## Скриншот / Demo

Фронтенд: бежевый фон, чёрные карточки, акцентный зелёный, крупная типографика Inter.  
Стриминг ответа отображается инкрементально — сырой JSON никогда не виден пользователю.

---

## Стек

| Слой | Технология |
|---|---|
| Backend | FastAPI (async) + Pydantic v2 + pydantic-settings |
| LLM | Anthropic SDK (Claude `claude-haiku-4-5`), prompt caching, SSE streaming |
| Frontend | React 18 CDN + marked.js + highlight.js + DOMPurify — **один** `static/index.html`, без сборщика |
| Dev | uv, ruff, mypy --strict, pytest (21 тест) |

---

## Быстрый старт

### 1. Клонировать и установить зависимости

```bash
git clone https://github.com/FrostWillmott/Relay.git
cd Relay
uv sync
```

### 2. Создать `.env` с ключом

```bash
cp .env.example .env
# Откройте .env и вставьте ваш ANTHROPIC_API_KEY
```

### 3. Запустить сервер

```bash
uv run uvicorn main:app --host 127.0.0.1 --port 8000 --env-file .env
```

Откройте [http://localhost:8000](http://localhost:8000) в браузере.

---

## Архитектура (3 слоя)

```
routers/      — HTTP только: парсинг, вызов сервиса, маппинг ошибок
services/     — бизнес-логика: санитизация, оркестрация LLM, валидация вывода
providers/    — Protocol-обёртка над Anthropic SDK (asyncio.to_thread + tenacity)
```

Потоковый путь `/ask/stream`:
- `_sync_stream` — двухсостоянная state-машина, извлекает только поле `answer` из JSON-стрима
- `stream_complete` — async-генератор с retry-циклом (3 попытки, `2^n` back-off для 429/5xx)
- `ask_stream_llm` — сервисный слой: sanitize → build_message → stream → history

Подробнее: [`TECHNICAL_DECISIONS.md`](TECHNICAL_DECISIONS.md) — 14 архитектурных решений с trade-offs.

---

## Проверка качества

```bash
# Все проверки одной командой
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict app/ main.py && uv run pytest tests/ -v
```

Ожидаемый результат: `ruff` — 0 ошибок, `mypy` — 0 ошибок на 16 файлах, `pytest` — 21/21 тестов зелёных.

---

## Ключевые фичи

- **Специализированный промпт**: ассистент команды разработчиков, краткие структурированные ответы, JSON-схема вывода, изоляция пользовательского ввода в `<USER_INPUT>` с явным приоритетом системного промпта
- **Prompt caching**: `cache_control: ephemeral` на системном промпте — снижение стоимости повторных вызовов
- **SSE Streaming**: typewriter-эффект без показа сырого JSON — state-машина декодирует только поле `answer`
- **Retry**: 3 попытки с exponential back-off на 429/5xx; 4xx — без ретрая
- **Валидация вывода**: Pydantic `LLMOutput` + repair-loop на `/ask`; state-машина на `/ask/stream`
- **Prompt injection mitigation**: нейтрализация (не удаление) маркеров инъекций + `<USER_INPUT>` делимитер
- **Error states**: 429 → «Слишком много запросов», 503 → «AI не настроен», 504 → «Таймаут», network → «Нет сети»
