"""SSE streaming utilities — helpers for Server-Sent Events responses."""

from __future__ import annotations

from typing import AsyncIterator

from fastapi.responses import StreamingResponse


def sse_response(generator: AsyncIterator[bytes]) -> StreamingResponse:
    """Wrap an async byte generator as a proper SSE streaming response."""
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
