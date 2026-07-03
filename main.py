"""FastAPI application factory."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.providers.anthropic import AnthropicProvider
from app.routers import ask

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Wire together config, provider, routers, and static files."""
    application = FastAPI(title="Relay", version="0.1.0")

    logger.info(
        "Starting Relay with model=%s timeout=%.1fs",
        settings.llm_model,
        settings.llm_timeout_sec,
    )

    provider = AnthropicProvider(
        api_key=settings.anthropic_api_key,
        model=settings.llm_model,
        timeout=settings.llm_timeout_sec,
    )
    application.state.provider = provider

    application.include_router(ask.router)

    @application.get("/favicon.ico", include_in_schema=False)
    @application.get("/apple-touch-icon.png", include_in_schema=False)
    @application.get(
        "/apple-touch-icon-precomposed.png", include_in_schema=False
    )
    async def favicon() -> Response:
        return Response(status_code=204)

    static_dir = Path(__file__).parent / "static"
    application.mount(
        "/", StaticFiles(directory=str(static_dir), html=True), name="static"
    )

    return application


app = create_app()
