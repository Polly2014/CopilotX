"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from copilotx import __version__
from copilotx.auth.token import TokenManager
from copilotx.proxy.client import CopilotClient


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage the CopilotClient lifecycle."""
    tm: TokenManager = app.state.token_manager
    token = await tm.ensure_copilot_token()
    client = CopilotClient(token)
    await client.__aenter__()
    app.state.client = client
    app.state.token_manager = tm
    try:
        yield
    finally:
        await client.__aexit__(None, None, None)


def create_app(token_manager: TokenManager) -> FastAPI:
    """Create and configure the FastAPI application."""

    app = FastAPI(
        title="CopilotX",
        description="Local GitHub Copilot API proxy",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.token_manager = token_manager

    # Register routes
    from copilotx.server.routes_anthropic import router as anthropic_router
    from copilotx.server.routes_models import router as models_router
    from copilotx.server.routes_openai import router as openai_router

    app.include_router(openai_router)
    app.include_router(anthropic_router)
    app.include_router(models_router)

    return app


async def get_ready_client(app_state) -> CopilotClient:
    """Get a CopilotClient with a valid token, refreshing if needed."""
    tm: TokenManager = app_state.token_manager
    client: CopilotClient = app_state.client
    # Ensure token is fresh
    token = await tm.ensure_copilot_token()
    client.update_token(token)
    return client
