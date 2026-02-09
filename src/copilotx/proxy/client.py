"""Async HTTP client for the GitHub Copilot backend API."""

from __future__ import annotations

import time
from typing import Any, AsyncIterator

import httpx

from copilotx.config import (
    COPILOT_CHAT_COMPLETIONS,
    COPILOT_HEADERS,
    COPILOT_MODELS,
    MODELS_CACHE_TTL,
    REQUEST_TIMEOUT,
)


class CopilotClient:
    """Async client that talks to api.githubcopilot.com."""

    def __init__(self, copilot_token: str) -> None:
        self._token = copilot_token
        self._client: httpx.AsyncClient | None = None
        # Model cache
        self._models_cache: list[dict] | None = None
        self._models_cache_time: float = 0

    async def __aenter__(self) -> "CopilotClient":
        self._client = httpx.AsyncClient(timeout=REQUEST_TIMEOUT)
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._client:
            await self._client.aclose()

    def update_token(self, token: str) -> None:
        """Update the Copilot JWT (called after token refresh)."""
        self._token = token

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        h = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            **COPILOT_HEADERS,
        }
        if extra:
            h.update(extra)
        return h

    # ── Models ──────────────────────────────────────────────────────

    async def list_models(self) -> list[dict]:
        """GET /models — returns list of available models (cached)."""
        now = time.time()
        if self._models_cache and (now - self._models_cache_time) < MODELS_CACHE_TTL:
            return self._models_cache

        assert self._client is not None
        resp = await self._client.get(COPILOT_MODELS, headers=self._headers())
        resp.raise_for_status()
        data = resp.json()

        models = [
            m
            for m in data.get("data", data.get("models", []))
            if m.get("model_picker_enabled", True)
        ]
        self._models_cache = models
        self._models_cache_time = now
        return models

    # ── Chat Completions (non-streaming) ────────────────────────────

    async def chat_completions(self, payload: dict) -> dict:
        """POST /chat/completions — non-streaming."""
        assert self._client is not None
        resp = await self._client.post(
            COPILOT_CHAT_COMPLETIONS,
            json=payload,
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()

    # ── Chat Completions (streaming) ────────────────────────────────

    async def chat_completions_stream(self, payload: dict) -> AsyncIterator[bytes]:
        """POST /chat/completions with stream=true — yields raw SSE lines."""
        assert self._client is not None
        payload["stream"] = True

        async with self._client.stream(
            "POST",
            COPILOT_CHAT_COMPLETIONS,
            json=payload,
            headers=self._headers(),
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line:
                    yield (line + "\n").encode("utf-8")
            # Ensure final newline
            yield b"\n"
