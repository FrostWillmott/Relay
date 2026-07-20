# ---- builder stage ----
FROM python:3.12-slim AS builder

WORKDIR /app

# Install uv for fast, reproducible dependency resolution.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install dependencies into a virtual environment.
# Layers: pyproject + lock first (cached when deps don't change), then sync.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# ---- runtime stage ----
FROM python:3.12-slim AS runtime

WORKDIR /app

# Copy the pre-built venv from the builder.
COPY --from=builder /app/.venv /app/.venv

# Copy application source and static assets.
COPY main.py ./
COPY app/ ./app/
COPY static/ ./static/

# Ensure the venv is used for all Python invocations.
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# ANTHROPIC_API_KEY is required at runtime — pass via `docker run --env`.
# All other settings (LLM_MODEL, MAX_INPUT_LEN, LLM_TIMEOUT) have defaults
# in app/config.py and can be overridden with --env as well.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
