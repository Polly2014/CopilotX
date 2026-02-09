"""OpenAI-compatible routes: /v1/chat/completions.

This is nearly a direct passthrough to api.githubcopilot.com since
the Copilot backend already speaks OpenAI format.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from copilotx.proxy.streaming import sse_response
from copilotx.server.app import get_ready_client

router = APIRouter(tags=["OpenAI"])


@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """OpenAI-compatible chat completions endpoint.

    Supports both streaming (stream=true) and non-streaming requests.
    """
    body = await request.json()
    client = await get_ready_client(request.app.state)

    try:
        if body.get("stream", False):
            return sse_response(client.chat_completions_stream(body))
        else:
            result = await client.chat_completions(body)
            return JSONResponse(content=result)
    except Exception as e:
        return JSONResponse(
            status_code=502,
            content={
                "error": {
                    "message": f"Copilot backend error: {e}",
                    "type": "upstream_error",
                }
            },
        )
