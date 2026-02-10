"""Async HTTP client for the GitHub Copilot backend API."""

from __future__ import annotations

import json
import time
from typing import Any, AsyncIterator

import httpx

from copilotx.config import (
    COPILOT_API_BASE_FALLBACK,
    COPILOT_CHAT_COMPLETIONS_PATH,
    COPILOT_HEADERS,
    COPILOT_MODELS_PATH,
    COPILOT_RESPONSES_PATH,
    MODELS_CACHE_TTL,
    REQUEST_TIMEOUT,
)


class CopilotClient:
    """Async client that talks to the Copilot API (dynamic base URL)."""

    def __init__(self, copilot_token: str, api_base_url: str = "") -> None:
        self._token = copilot_token
        self._api_base = (api_base_url or COPILOT_API_BASE_FALLBACK).rstrip("/")
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

    def update_api_base(self, api_base_url: str) -> None:
        """Update the API base URL (called after token refresh if changed)."""
        if api_base_url:
            self._api_base = api_base_url.rstrip("/")

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
        url = f"{self._api_base}{COPILOT_MODELS_PATH}"
        resp = await self._client.get(url, headers=self._headers())
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
        url = f"{self._api_base}{COPILOT_CHAT_COMPLETIONS_PATH}"
        resp = await self._client.post(url, json=payload, headers=self._headers())
        resp.raise_for_status()
        return resp.json()

    # ── Chat Completions (streaming) ────────────────────────────────

    async def chat_completions_stream(self, payload: dict) -> AsyncIterator[bytes]:
        """POST /chat/completions with stream=true — yields raw SSE lines."""
        assert self._client is not None
        payload["stream"] = True
        url = f"{self._api_base}{COPILOT_CHAT_COMPLETIONS_PATH}"

        async with self._client.stream(
            "POST", url, json=payload, headers=self._headers(),
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line:
                    yield (line + "\n").encode("utf-8")
            # Ensure final newline
            yield b"\n"

    # ── Responses API (non-streaming) ───────────────────────────────

    async def responses(
        self,
        payload: dict,
        *,
        vision: bool = False,
        initiator: str = "user",
    ) -> dict:
        """POST /responses — OpenAI Responses API (non-streaming)."""
        assert self._client is not None
        url = f"{self._api_base}{COPILOT_RESPONSES_PATH}"
        extra_headers = self._responses_extra_headers(vision, initiator)
        # Strip service_tier — not supported by GitHub Copilot
        payload.pop("service_tier", None)

        resp = await self._client.post(
            url, json=payload, headers=self._headers(extra_headers),
        )
        resp.raise_for_status()
        return resp.json()

    # ── Responses API (streaming) ───────────────────────────────────

    async def responses_stream(
        self,
        payload: dict,
        *,
        vision: bool = False,
        initiator: str = "user",
    ) -> AsyncIterator[bytes]:
        """POST /responses with stream=true — yields raw SSE lines."""
        assert self._client is not None
        payload["stream"] = True
        # Strip service_tier — not supported by GitHub Copilot
        payload.pop("service_tier", None)
        url = f"{self._api_base}{COPILOT_RESPONSES_PATH}"
        extra_headers = self._responses_extra_headers(vision, initiator)

        async with self._client.stream(
            "POST", url, json=payload, headers=self._headers(extra_headers),
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line:
                    yield (line + "\n").encode("utf-8")
            yield b"\n"

    # ── Private helpers ─────────────────────────────────────────────

    @staticmethod
    def _responses_extra_headers(vision: bool, initiator: str) -> dict[str, str]:
        """Build extra headers for Responses API requests."""
        h: dict[str, str] = {"X-Initiator": initiator}
        if vision:
            h["copilot-vision-request"] = "true"
        return h
