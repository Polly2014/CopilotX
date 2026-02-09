"""Anthropic ↔ OpenAI format translator.

Copilot backend speaks OpenAI format natively.  This module translates:
  - Anthropic request  → OpenAI request   (inbound)
  - OpenAI response    → Anthropic response (outbound)
  - OpenAI SSE chunks  → Anthropic SSE events (streaming)
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, AsyncIterator


# ═══════════════════════════════════════════════════════════════════
#  REQUEST: Anthropic → OpenAI
# ═══════════════════════════════════════════════════════════════════


def anthropic_to_openai_request(body: dict) -> dict:
    """Convert an Anthropic /v1/messages request to OpenAI /chat/completions format."""
    messages: list[dict[str, Any]] = []

    # System message
    system = body.get("system")
    if system:
        if isinstance(system, str):
            messages.append({"role": "system", "content": system})
        elif isinstance(system, list):
            # Anthropic allows system as list of content blocks
            text_parts = [b["text"] for b in system if b.get("type") == "text"]
            if text_parts:
                messages.append({"role": "system", "content": "\n".join(text_parts)})

    # Convert messages
    for msg in body.get("messages", []):
        role = msg["role"]
        content = msg.get("content")

        if isinstance(content, str):
            messages.append({"role": role, "content": content})
        elif isinstance(content, list):
            # Anthropic content blocks → flatten text
            text_parts = []
            for block in content:
                if isinstance(block, str):
                    text_parts.append(block)
                elif block.get("type") == "text":
                    text_parts.append(block["text"])
                # TODO: handle image blocks, tool_use, tool_result
            messages.append({"role": role, "content": "\n".join(text_parts)})
        else:
            messages.append({"role": role, "content": str(content) if content else ""})

    # Build OpenAI request
    openai_req: dict[str, Any] = {
        "model": body.get("model", "gpt-4o"),
        "messages": messages,
    }

    # Map parameters
    if "max_tokens" in body:
        openai_req["max_tokens"] = body["max_tokens"]
    if "temperature" in body:
        openai_req["temperature"] = body["temperature"]
    if "top_p" in body:
        openai_req["top_p"] = body["top_p"]
    if "stop_sequences" in body:
        openai_req["stop"] = body["stop_sequences"]
    if "stream" in body:
        openai_req["stream"] = body["stream"]

    return openai_req


# ═══════════════════════════════════════════════════════════════════
#  RESPONSE: OpenAI → Anthropic (non-streaming)
# ═══════════════════════════════════════════════════════════════════


def openai_to_anthropic_response(openai_resp: dict, model: str) -> dict:
    """Convert an OpenAI chat completion response to Anthropic /v1/messages format."""
    choice = openai_resp.get("choices", [{}])[0]
    message = choice.get("message", {})
    content_text = message.get("content", "")

    # Map finish_reason
    finish_reason = choice.get("finish_reason", "end_turn")
    stop_reason_map = {
        "stop": "end_turn",
        "length": "max_tokens",
        "content_filter": "end_turn",
        "tool_calls": "tool_use",
    }
    stop_reason = stop_reason_map.get(finish_reason, "end_turn")

    # Usage
    usage = openai_resp.get("usage", {})

    return {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": content_text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


# ═══════════════════════════════════════════════════════════════════
#  STREAMING: OpenAI SSE → Anthropic SSE
# ═══════════════════════════════════════════════════════════════════


async def openai_stream_to_anthropic_stream(
    openai_lines: AsyncIterator[bytes],
    model: str,
) -> AsyncIterator[bytes]:
    """Translate OpenAI SSE stream to Anthropic SSE stream format.

    Anthropic streaming protocol:
      event: message_start       → message metadata
      event: content_block_start → start of content block
      event: content_block_delta → incremental text
      event: content_block_stop  → end of content block
      event: message_delta       → stop reason + usage
      event: message_stop        → end of message
    """
    msg_id = f"msg_{uuid.uuid4().hex[:24]}"
    input_tokens = 0
    output_tokens = 0
    sent_start = False
    sent_block_start = False

    async for raw_line in openai_lines:
        line = raw_line.decode("utf-8").strip()

        if not line or not line.startswith("data: "):
            continue

        data_str = line[6:]  # strip "data: "
        if data_str == "[DONE]":
            break

        try:
            chunk = json.loads(data_str)
        except json.JSONDecodeError:
            continue

        # Emit message_start once
        if not sent_start:
            start_event = {
                "type": "message_start",
                "message": {
                    "id": msg_id,
                    "type": "message",
                    "role": "assistant",
                    "model": model,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                },
            }
            yield _sse_event("message_start", start_event)
            sent_start = True

        # Extract delta content
        delta = chunk.get("choices", [{}])[0].get("delta", {})
        content = delta.get("content")
        finish_reason = chunk.get("choices", [{}])[0].get("finish_reason")

        if content:
            # Emit content_block_start once
            if not sent_block_start:
                block_start = {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                }
                yield _sse_event("content_block_start", block_start)
                sent_block_start = True

            # Emit content_block_delta
            block_delta = {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": content},
            }
            yield _sse_event("content_block_delta", block_delta)

        # Track usage if present
        if "usage" in chunk:
            input_tokens = chunk["usage"].get("prompt_tokens", input_tokens)
            output_tokens = chunk["usage"].get("completion_tokens", output_tokens)

    # Finalize — close block + message
    if sent_block_start:
        yield _sse_event("content_block_stop", {"type": "content_block_stop", "index": 0})

    yield _sse_event(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": output_tokens},
        },
    )
    yield _sse_event("message_stop", {"type": "message_stop"})


def _sse_event(event_type: str, data: dict) -> bytes:
    """Format a single Anthropic SSE event."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n".encode("utf-8")
