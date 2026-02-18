"""OpenAI-compatible routes: /v1/chat/completions.

This is nearly a direct passthrough to api.githubcopilot.com since
the Copilot backend already speaks OpenAI format.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from copilotx.proxy.streaming import sse_response
from copilotx.server.app import get_ready_client

logger = logging.getLogger(__name__)

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
        logger.error("Chat completions error: %s", e)
        status_code = 502
        error_content = {
            "error": {
                "message": f"Copilot backend error: {e}",
                "type": "upstream_error",
            }
        }
        if hasattr(e, 'response') and e.response is not None:
            status_code = e.response.status_code
            try:
                # Try to parse and forward the backend's JSON error
                error_content = json.loads(e.response.text)
            except (json.JSONDecodeError, ValueError):
                error_content["error"]["message"] = e.response.text[:500]
        return JSONResponse(status_code=status_code, content=error_content)
