"""OpenAI Responses API route: /v1/responses.

Implements the OpenAI Responses API with:
  - Vision content detection → copilot-vision-request header
  - Agent initiator detection → X-Initiator header
  - apply_patch tool patching → custom→function type conversion
  - Stream ID synchronization → fix inconsistent item IDs
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from copilotx.proxy.responses_stream import fix_responses_stream
from copilotx.proxy.streaming import sse_response
from copilotx.server.app import get_ready_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Responses"])


@router.post("/v1/responses")
async def responses(request: Request):
    """OpenAI Responses API endpoint.

    Supports both streaming (stream=true) and non-streaming requests.
    Applies vision detection, initiator detection, and apply_patch patching.
    """
    body = await request.json()
    client = await get_ready_client(request.app.state)

    # Detect vision content and initiator role
    vision = has_vision_input(body)
    initiator = "agent" if has_agent_initiator(body) else "user"

    # Patch apply_patch tool (custom → function type)
    patch_apply_patch_tool(body)

    try:
        if body.get("stream", False):
            raw_stream = client.responses_stream(
                body, vision=vision, initiator=initiator,
            )
            # Apply stream ID synchronization
            fixed_stream = fix_responses_stream(raw_stream)
            return sse_response(fixed_stream)
        else:
            result = await client.responses(
                body, vision=vision, initiator=initiator,
            )
            return JSONResponse(content=result)
    except Exception as e:
        logger.error("Responses API error: %s", e)
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


# ═══════════════════════════════════════════════════════════════════
#  Helper Functions
# ═══════════════════════════════════════════════════════════════════


def has_vision_input(body: dict) -> bool:
    """Check if the request input contains image/vision content."""
    input_data = body.get("input")
    if not isinstance(input_data, list):
        return False

    for item in input_data:
        if not isinstance(item, dict):
            continue
        # Check message content parts for images
        content = item.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") in (
                    "input_image",
                    "image",
                    "image_url",
                ):
                    return True
    return False


def has_agent_initiator(body: dict) -> bool:
    """Check if the last input item indicates an agent (vs user) initiator."""
    input_data = body.get("input")
    if not isinstance(input_data, list) or not input_data:
        return False

    last_item = input_data[-1]
    if not isinstance(last_item, dict):
        return False

    role = last_item.get("role", "").lower()
    item_type = last_item.get("type", "").lower()

    # Assistant messages and function-related items are agent-initiated
    if role == "assistant":
        return True
    if item_type in ("function_call", "function_call_output", "reasoning"):
        return True

    return False


def patch_apply_patch_tool(body: dict) -> None:
    """Patch custom-type apply_patch tools to function type (in-place).

    Some clients (e.g., Codex) send apply_patch as a "custom" type tool,
    but GitHub Copilot's API expects it as a "function" type tool.
    """
    tools = body.get("tools")
    if not isinstance(tools, list):
        return

    for tool in tools:
        if not isinstance(tool, dict):
            continue
        if tool.get("type") == "custom" and tool.get("name") == "apply_patch":
            tool["type"] = "function"
            tool["description"] = "Use the `apply_patch` tool to edit files"
            tool["parameters"] = {
                "type": "object",
                "properties": {
                    "input": {
                        "type": "string",
                        "description": "The entire contents of the apply_patch command",
                    }
                },
                "required": ["input"],
            }
            tool["strict"] = False
