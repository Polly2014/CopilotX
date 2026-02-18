"""Anthropic-compatible routes: /v1/messages.

Translates Anthropic format requests to OpenAI format (which is what
the Copilot backend speaks), and translates responses back.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from copilotx.proxy.streaming import sse_response
from copilotx.proxy.translator import (
    anthropic_to_openai_request,
    openai_stream_to_anthropic_stream,
    openai_to_anthropic_response,
)
from copilotx.server.app import get_ready_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Anthropic"])


@router.post("/v1/messages")
async def messages(request: Request):
    """Anthropic-compatible messages endpoint.

    Accepts Anthropic format, translates to OpenAI, calls Copilot backend,
    and translates the response back to Anthropic format.
    """
    body = await request.json()
    model = body.get("model", "gpt-4o")
    is_stream = body.get("stream", False)

    # Log the incoming request for debugging
    logger.info(
        "Anthropic request: model=%s stream=%s max_tokens=%s tools=%d keys=%s",
        model,
        is_stream,
        body.get("max_tokens"),
        len(body.get("tools", [])),
        list(body.keys()),
    )

    # Translate Anthropic request → OpenAI request
    openai_payload = anthropic_to_openai_request(body)

    client = await get_ready_client(request.app.state)

    try:
        if is_stream:
            # Stream: OpenAI SSE → Anthropic SSE
            openai_stream = client.chat_completions_stream(openai_payload)
            anthropic_stream = openai_stream_to_anthropic_stream(openai_stream, model)
            return sse_response(anthropic_stream)
        else:
            # Non-stream: translate response
            openai_resp = await client.chat_completions(openai_payload)
            anthropic_resp = openai_to_anthropic_response(openai_resp, model)
            return JSONResponse(content=anthropic_resp)
    except Exception as e:
        logger.error("Copilot backend error: %s", e)
        status_code = 502
        error_content = {
            "type": "error",
            "error": {
                "type": "upstream_error",
                "message": f"Copilot backend error: {e}",
            },
        }
        if hasattr(e, 'response') and e.response is not None:
            status_code = e.response.status_code
            try:
                # Try to parse backend JSON error and extract message
                backend_error = json.loads(e.response.text)
                if "error" in backend_error:
                    error_content["error"]["message"] = backend_error["error"].get("message", str(backend_error["error"]))
                else:
                    error_content["error"]["message"] = e.response.text[:500]
            except (json.JSONDecodeError, ValueError):
                error_content["error"]["message"] = e.response.text[:500]
        return JSONResponse(status_code=status_code, content=error_content)
