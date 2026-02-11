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
#  MODEL MAPPING: Anthropic model names → Copilot model names
# ═══════════════════════════════════════════════════════════════════

# Anthropic model names (sent by Claude Code) → Copilot-compatible names
ANTHROPIC_TO_COPILOT_MODEL_MAP = {
    # Claude Sonnet 4.5 variants
    "claude-sonnet-4-5-20250929": "claude-sonnet-4.5",
    "claude-sonnet-4.5-20250929": "claude-sonnet-4.5",
    "claude-4-5-sonnet": "claude-sonnet-4.5",
    "claude-4.5-sonnet": "claude-sonnet-4.5",
    # Claude Sonnet 4 variants
    "claude-sonnet-4-20250514": "claude-sonnet-4",
    "claude-sonnet-4": "claude-sonnet-4",
    "claude-4-sonnet": "claude-sonnet-4",
    # Claude Opus 4.5 variants
    "claude-opus-4-5-20250929": "claude-opus-4.5",
    "claude-opus-4.5-20250929": "claude-opus-4.5",
    "claude-4-5-opus": "claude-opus-4.5",
    "claude-4.5-opus": "claude-opus-4.5",
    # Claude Opus 4.6 variants
    "claude-opus-4-6": "claude-opus-4.6",
    "claude-opus-4.6": "claude-opus-4.6",
    "claude-4-6-opus": "claude-opus-4.6",
    "claude-4.6-opus": "claude-opus-4.6",
    # Claude Opus 4 variants
    "claude-opus-4-20250514": "claude-opus-41",
    "claude-opus-4": "claude-opus-41",
    "claude-4-opus": "claude-opus-41",
    # Claude Haiku 4.5 variants
    "claude-haiku-4-5": "claude-haiku-4.5",
    "claude-haiku-4.5": "claude-haiku-4.5",
    "claude-4-5-haiku": "claude-haiku-4.5",
    "claude-4.5-haiku": "claude-haiku-4.5",
    # Claude 3.5 Sonnet (older naming)
    "claude-3-5-sonnet-20241022": "claude-sonnet-4",
    "claude-3-5-sonnet-20240620": "claude-sonnet-4",
    "claude-3-5-sonnet": "claude-sonnet-4",
    "claude-3.5-sonnet": "claude-sonnet-4",
    # Claude 3 Opus (older naming)
    "claude-3-opus-20240229": "claude-opus-41",
    "claude-3-opus": "claude-opus-41",
    "claude-3.0-opus": "claude-opus-41",
    # Claude 3 Haiku
    "claude-3-haiku-20240307": "claude-haiku-4.5",
    "claude-3-haiku": "claude-haiku-4.5",
    "claude-3.0-haiku": "claude-haiku-4.5",
}


def map_anthropic_model_to_copilot(model: str) -> str:
    """Map Anthropic model names to Copilot-compatible model names.
    
    Claude Code sends Anthropic-style model names like 'claude-sonnet-4-5-20250929',
    but Copilot API expects names like 'claude-sonnet-4.5'.
    """
    # Direct mapping
    if model in ANTHROPIC_TO_COPILOT_MODEL_MAP:
        return ANTHROPIC_TO_COPILOT_MODEL_MAP[model]
    
    # If already a Copilot-compatible name (has dots like 4.5), return as-is
    if "." in model:
        return model
    
    # Fuzzy matching for unknown variants
    model_lower = model.lower()
    if "sonnet" in model_lower:
        if "4-5" in model_lower or "4.5" in model_lower:
            return "claude-sonnet-4.5"
        return "claude-sonnet-4"
    if "opus" in model_lower:
        if "4-6" in model_lower or "4.6" in model_lower:
            return "claude-opus-4.6"
        if "4-5" in model_lower or "4.5" in model_lower:
            return "claude-opus-4.5"
        return "claude-opus-41"
    if "haiku" in model_lower:
        return "claude-haiku-4.5"
    
    # Fall back to original model name (might be GPT model etc.)
    return model


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
            # Anthropic content blocks → OpenAI content parts
            openai_parts: list[dict[str, Any]] = []
            has_non_text = False
            for block in content:
                if isinstance(block, str):
                    openai_parts.append({"type": "text", "text": block})
                elif block.get("type") == "text":
                    openai_parts.append({"type": "text", "text": block["text"]})
                elif block.get("type") == "image":
                    # Anthropic image block → OpenAI image_url
                    source = block.get("source", {})
                    if source.get("type") == "base64":
                        media_type = source.get("media_type", "image/png")
                        data_b64 = source.get("data", "")
                        openai_parts.append({
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{media_type};base64,{data_b64}",
                            },
                        })
                        has_non_text = True
                    elif source.get("type") == "url":
                        openai_parts.append({
                            "type": "image_url",
                            "image_url": {"url": source.get("url", "")},
                        })
                        has_non_text = True
                elif block.get("type") == "tool_use":
                    # Anthropic tool_use → skip in messages (handled separately)
                    pass
                elif block.get("type") == "tool_result":
                    # Anthropic tool_result → skip in messages (handled separately)
                    pass

            if has_non_text or len(openai_parts) > 1:
                # Multi-modal content — use OpenAI array format
                messages.append({"role": role, "content": openai_parts})
            else:
                # Text only — flatten to string
                text = "\n".join(
                    p["text"] for p in openai_parts if p.get("type") == "text"
                )
                messages.append({"role": role, "content": text})
        else:
            messages.append({"role": role, "content": str(content) if content else ""})

    # Map Anthropic model name to Copilot-compatible name
    anthropic_model = body.get("model", "gpt-4o")
    copilot_model = map_anthropic_model_to_copilot(anthropic_model)

    # Build OpenAI request
    openai_req: dict[str, Any] = {
        "model": copilot_model,
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
