"""Anthropic ↔ OpenAI format translator.

Copilot backend speaks OpenAI format natively.  This module translates:
  - Anthropic request  → OpenAI request   (inbound)
  - OpenAI response    → Anthropic response (outbound)
  - OpenAI SSE chunks  → Anthropic SSE events (streaming)

Includes full tool/function-calling support:
  - Anthropic tools definitions  → OpenAI tools/functions
  - Anthropic tool_use blocks    → OpenAI tool_calls
  - Anthropic tool_result blocks → OpenAI tool role messages
  - OpenAI tool_calls response   → Anthropic tool_use content blocks
  - OpenAI tool_calls streaming  → Anthropic tool_use SSE events
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)


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
    """Convert an Anthropic /v1/messages request to OpenAI /chat/completions format.

    Handles:
      - system messages (string or content-block list)
      - text / image content blocks
      - tool_use blocks → OpenAI assistant tool_calls
      - tool_result blocks → OpenAI tool-role messages
      - tools definitions → OpenAI tools/functions
      - tool_choice → OpenAI tool_choice
    """
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
            # Separate tool-related blocks from regular content
            text_parts: list[dict[str, Any]] = []
            tool_use_blocks: list[dict[str, Any]] = []
            tool_result_blocks: list[dict[str, Any]] = []
            has_non_text = False

            for block in content:
                if isinstance(block, str):
                    text_parts.append({"type": "text", "text": block})
                elif block.get("type") == "text":
                    text_parts.append({"type": "text", "text": block["text"]})
                elif block.get("type") == "image":
                    # Anthropic image block → OpenAI image_url
                    source = block.get("source", {})
                    if source.get("type") == "base64":
                        media_type = source.get("media_type", "image/png")
                        data_b64 = source.get("data", "")
                        text_parts.append({
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{media_type};base64,{data_b64}",
                            },
                        })
                        has_non_text = True
                    elif source.get("type") == "url":
                        text_parts.append({
                            "type": "image_url",
                            "image_url": {"url": source.get("url", "")},
                        })
                        has_non_text = True
                elif block.get("type") == "tool_use":
                    tool_use_blocks.append(block)
                elif block.get("type") == "tool_result":
                    tool_result_blocks.append(block)

            # --- Handle assistant messages with tool_use blocks ---
            if role == "assistant" and tool_use_blocks:
                # Build the assistant message with tool_calls
                assistant_msg: dict[str, Any] = {"role": "assistant"}

                # Text content (may be None if assistant only calls tools)
                if text_parts:
                    text_content = "\n".join(
                        p["text"] for p in text_parts if p.get("type") == "text"
                    )
                    assistant_msg["content"] = text_content if text_content else None
                else:
                    assistant_msg["content"] = None

                # Convert tool_use blocks → OpenAI tool_calls
                assistant_msg["tool_calls"] = [
                    {
                        "id": tu.get("id", f"call_{uuid.uuid4().hex[:24]}"),
                        "type": "function",
                        "function": {
                            "name": tu["name"],
                            "arguments": json.dumps(tu.get("input", {})),
                        },
                    }
                    for tu in tool_use_blocks
                ]
                messages.append(assistant_msg)

            # --- Handle user messages with tool_result blocks ---
            elif tool_result_blocks:
                # If there's also regular text content, add it first
                if text_parts:
                    if has_non_text or len(text_parts) > 1:
                        messages.append({"role": role, "content": text_parts})
                    else:
                        text = "\n".join(
                            p["text"] for p in text_parts if p.get("type") == "text"
                        )
                        if text:
                            messages.append({"role": role, "content": text})

                # Convert each tool_result → OpenAI tool message
                for tr in tool_result_blocks:
                    tool_content = tr.get("content", "")
                    # Anthropic tool_result content can be string or list of blocks
                    if isinstance(tool_content, list):
                        parts = []
                        for tc_block in tool_content:
                            if isinstance(tc_block, str):
                                parts.append(tc_block)
                            elif tc_block.get("type") == "text":
                                parts.append(tc_block["text"])
                        tool_content = "\n".join(parts)
                    elif not isinstance(tool_content, str):
                        tool_content = json.dumps(tool_content)

                    tool_msg: dict[str, Any] = {
                        "role": "tool",
                        "tool_call_id": tr.get("tool_use_id", ""),
                        "content": tool_content,
                    }
                    # Propagate error status
                    if tr.get("is_error"):
                        # OpenAI doesn't have a direct equivalent; embed in content
                        tool_msg["content"] = f"[ERROR] {tool_content}"
                    messages.append(tool_msg)

            # --- Regular content (no tool blocks) ---
            else:
                if has_non_text or len(text_parts) > 1:
                    messages.append({"role": role, "content": text_parts})
                else:
                    text = "\n".join(
                        p["text"] for p in text_parts if p.get("type") == "text"
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

    # ── Tools conversion ────────────────────────────────────────
    if "tools" in body:
        openai_req["tools"] = _convert_anthropic_tools(body["tools"])
        logger.debug(
            "Converted %d Anthropic tools → OpenAI format", len(body["tools"])
        )

    # ── tool_choice conversion ──────────────────────────────────
    if "tool_choice" in body:
        openai_req["tool_choice"] = _convert_anthropic_tool_choice(
            body["tool_choice"]
        )

    return openai_req


def _convert_anthropic_tools(tools: list[dict]) -> list[dict]:
    """Convert Anthropic tools definitions to OpenAI tools format.

    Anthropic:  {"name": ..., "description": ..., "input_schema": {...}}
    OpenAI:     {"type": "function", "function": {"name": ..., "description": ..., "parameters": {...}}}
    """
    openai_tools = []
    for tool in tools:
        # Handle different Anthropic tool types
        tool_type = tool.get("type", "custom")

        if tool_type in ("computer_20241022", "bash_20241022", "text_editor_20241022"):
            # Anthropic built-in tools — convert to function calls
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": tool.get("name", tool_type),
                    "description": tool.get("description", f"Anthropic {tool_type} tool"),
                    "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
                },
            })
        else:
            # Standard custom tool
            func_def: dict[str, Any] = {
                "name": tool["name"],
            }
            if "description" in tool:
                func_def["description"] = tool["description"]

            # input_schema → parameters
            schema = tool.get("input_schema", {})
            if schema:
                func_def["parameters"] = schema
            else:
                func_def["parameters"] = {"type": "object", "properties": {}}

            openai_tools.append({
                "type": "function",
                "function": func_def,
            })

    return openai_tools


def _convert_anthropic_tool_choice(tool_choice: Any) -> Any:
    """Convert Anthropic tool_choice to OpenAI tool_choice.

    Anthropic:                    OpenAI:
      {"type": "auto"}       →    "auto"
      {"type": "any"}        →    "required"
      {"type": "tool", "name": X} → {"type": "function", "function": {"name": X}}
      "auto" (string)        →    "auto"
      "any"  (string)        →    "required"
      "none" (string)        →    "none"
    """
    if isinstance(tool_choice, str):
        if tool_choice == "any":
            return "required"
        return tool_choice  # "auto", "none"

    if isinstance(tool_choice, dict):
        tc_type = tool_choice.get("type", "auto")
        if tc_type == "auto":
            return "auto"
        if tc_type == "any":
            return "required"
        if tc_type == "none":
            return "none"
        if tc_type == "tool":
            return {
                "type": "function",
                "function": {"name": tool_choice["name"]},
            }
    return "auto"


# ═══════════════════════════════════════════════════════════════════
#  RESPONSE: OpenAI → Anthropic (non-streaming)
# ═══════════════════════════════════════════════════════════════════


def openai_to_anthropic_response(openai_resp: dict, model: str) -> dict:
    """Convert an OpenAI chat completion response to Anthropic /v1/messages format.

    Handles text content, tool_calls, and mixed responses.
    """
    choice = openai_resp.get("choices", [{}])[0]
    message = choice.get("message", {})
    content_text = message.get("content", "")
    tool_calls = message.get("tool_calls")

    # Build content blocks
    content_blocks: list[dict[str, Any]] = []

    # Add text block if present
    if content_text:
        content_blocks.append({"type": "text", "text": content_text})

    # Convert OpenAI tool_calls → Anthropic tool_use blocks
    if tool_calls:
        for tc in tool_calls:
            func = tc.get("function", {})
            # Parse arguments JSON string → dict
            try:
                tool_input = json.loads(func.get("arguments", "{}"))
            except (json.JSONDecodeError, TypeError):
                tool_input = {}

            content_blocks.append({
                "type": "tool_use",
                "id": tc.get("id", f"toolu_{uuid.uuid4().hex[:24]}"),
                "name": func.get("name", ""),
                "input": tool_input,
            })

    # Ensure at least one content block
    if not content_blocks:
        content_blocks.append({"type": "text", "text": ""})

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
        "content": content_blocks,
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
      event: content_block_delta → incremental text or tool input JSON
      event: content_block_stop  → end of content block
      event: message_delta       → stop reason + usage
      event: message_stop        → end of message

    Handles both text content and tool_calls streaming:
      - OpenAI delta.content       → Anthropic text_delta
      - OpenAI delta.tool_calls    → Anthropic tool_use content blocks
    """
    msg_id = f"msg_{uuid.uuid4().hex[:24]}"
    input_tokens = 0
    output_tokens = 0
    sent_start = False
    sent_text_block_start = False

    # Track tool call blocks: index → {id, name, block_index, started}
    tool_call_trackers: dict[int, dict[str, Any]] = {}
    # Next content_block index (0 = text, 1+ = tool_use)
    next_block_index = 0
    # Track the text block index
    text_block_index = 0
    # Whether we've seen any text content
    has_text = False
    # Accumulated finish_reason
    finish_reason = "end_turn"

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

        # Extract delta
        choice = chunk.get("choices", [{}])[0]
        delta = choice.get("delta", {})
        chunk_finish = choice.get("finish_reason")
        content = delta.get("content")
        tool_calls = delta.get("tool_calls")

        # Track finish reason
        if chunk_finish:
            if chunk_finish == "tool_calls":
                finish_reason = "tool_use"
            elif chunk_finish == "length":
                finish_reason = "max_tokens"
            else:
                finish_reason = "end_turn"

        # ── Handle text content ────────────────────────────────
        if content:
            has_text = True
            if not sent_text_block_start:
                text_block_index = next_block_index
                next_block_index += 1
                block_start = {
                    "type": "content_block_start",
                    "index": text_block_index,
                    "content_block": {"type": "text", "text": ""},
                }
                yield _sse_event("content_block_start", block_start)
                sent_text_block_start = True

            block_delta = {
                "type": "content_block_delta",
                "index": text_block_index,
                "delta": {"type": "text_delta", "text": content},
            }
            yield _sse_event("content_block_delta", block_delta)

        # ── Handle tool_calls streaming ────────────────────────
        if tool_calls:
            for tc_delta in tool_calls:
                tc_index = tc_delta.get("index", 0)
                tc_id = tc_delta.get("id")
                tc_func = tc_delta.get("function", {})
                tc_name = tc_func.get("name")
                tc_args = tc_func.get("arguments", "")

                if tc_index not in tool_call_trackers:
                    # Close text block first if still open
                    if sent_text_block_start and not any(
                        t.get("text_closed") for t in tool_call_trackers.values()
                    ) and not tool_call_trackers:
                        yield _sse_event(
                            "content_block_stop",
                            {"type": "content_block_stop", "index": text_block_index},
                        )

                    # New tool call — create tracker and emit content_block_start
                    block_idx = next_block_index
                    next_block_index += 1

                    tool_id = tc_id or f"toolu_{uuid.uuid4().hex[:24]}"
                    tool_name = tc_name or ""

                    tool_call_trackers[tc_index] = {
                        "id": tool_id,
                        "name": tool_name,
                        "block_index": block_idx,
                        "started": True,
                        "text_closed": True,
                    }

                    yield _sse_event("content_block_start", {
                        "type": "content_block_start",
                        "index": block_idx,
                        "content_block": {
                            "type": "tool_use",
                            "id": tool_id,
                            "name": tool_name,
                            "input": {},
                        },
                    })
                else:
                    # Update existing tracker with name if provided
                    tracker = tool_call_trackers[tc_index]
                    if tc_id and not tracker["id"]:
                        tracker["id"] = tc_id
                    if tc_name and not tracker["name"]:
                        tracker["name"] = tc_name

                # Emit argument deltas as input_json_delta
                if tc_args:
                    tracker = tool_call_trackers[tc_index]
                    yield _sse_event("content_block_delta", {
                        "type": "content_block_delta",
                        "index": tracker["block_index"],
                        "delta": {
                            "type": "input_json_delta",
                            "partial_json": tc_args,
                        },
                    })

        # Track usage if present
        if "usage" in chunk:
            input_tokens = chunk["usage"].get("prompt_tokens", input_tokens)
            output_tokens = chunk["usage"].get("completion_tokens", output_tokens)

    # ── Finalize — close all open blocks + message ─────────────

    # Close text block if it was opened and no tool calls closed it
    if sent_text_block_start and not tool_call_trackers:
        yield _sse_event(
            "content_block_stop",
            {"type": "content_block_stop", "index": text_block_index},
        )

    # Close all tool call blocks
    for _tc_idx, tracker in sorted(tool_call_trackers.items()):
        yield _sse_event(
            "content_block_stop",
            {"type": "content_block_stop", "index": tracker["block_index"]},
        )

    yield _sse_event(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": finish_reason, "stop_sequence": None},
            "usage": {"output_tokens": output_tokens},
        },
    )
    yield _sse_event("message_stop", {"type": "message_stop"})


def _sse_event(event_type: str, data: dict) -> bytes:
    """Format a single Anthropic SSE event."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n".encode("utf-8")
