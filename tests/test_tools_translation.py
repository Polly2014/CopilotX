"""Tests for Anthropic ↔ OpenAI tools translation.

Verifies that CopilotX correctly translates:
  1. Anthropic tool definitions → OpenAI tools/functions
  2. Anthropic tool_use blocks → OpenAI tool_calls
  3. Anthropic tool_result blocks → OpenAI tool messages
  4. OpenAI tool_calls response → Anthropic tool_use content blocks
  5. OpenAI tool_calls streaming → Anthropic tool_use SSE events
  6. tool_choice conversion
"""

import asyncio
import json

from copilotx.proxy.translator import (
    _convert_anthropic_tool_choice,
    _convert_anthropic_tools,
    anthropic_to_openai_request,
    openai_stream_to_anthropic_stream,
    openai_to_anthropic_response,
)


# ═══════════════════════════════════════════════════════════════════
#  1. Tools definitions conversion
# ═══════════════════════════════════════════════════════════════════


def test_tools_definition_conversion():
    """Anthropic tools → OpenAI tools."""
    anthropic_tools = [
        {
            "name": "read_file",
            "description": "Read a file from disk",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                },
                "required": ["path"],
            },
        },
        {
            "name": "write_file",
            "description": "Write content to a file",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    ]

    result = _convert_anthropic_tools(anthropic_tools)

    assert len(result) == 2
    assert result[0]["type"] == "function"
    assert result[0]["function"]["name"] == "read_file"
    assert result[0]["function"]["description"] == "Read a file from disk"
    assert result[0]["function"]["parameters"]["required"] == ["path"]

    assert result[1]["function"]["name"] == "write_file"
    assert len(result[1]["function"]["parameters"]["required"]) == 2

    print("✅ Tools definition conversion: PASSED")


# ═══════════════════════════════════════════════════════════════════
#  2. tool_choice conversion
# ═══════════════════════════════════════════════════════════════════


def test_tool_choice_conversion():
    """Anthropic tool_choice → OpenAI tool_choice."""
    # Auto
    assert _convert_anthropic_tool_choice({"type": "auto"}) == "auto"
    assert _convert_anthropic_tool_choice("auto") == "auto"

    # Any → required
    assert _convert_anthropic_tool_choice({"type": "any"}) == "required"
    assert _convert_anthropic_tool_choice("any") == "required"

    # None
    assert _convert_anthropic_tool_choice({"type": "none"}) == "none"
    assert _convert_anthropic_tool_choice("none") == "none"

    # Specific tool
    result = _convert_anthropic_tool_choice({"type": "tool", "name": "read_file"})
    assert result == {"type": "function", "function": {"name": "read_file"}}

    print("✅ tool_choice conversion: PASSED")


# ═══════════════════════════════════════════════════════════════════
#  3. Full request translation with tools
# ═══════════════════════════════════════════════════════════════════


def test_full_request_with_tools():
    """Complete Anthropic request with tools → OpenAI format."""
    anthropic_request = {
        "model": "claude-sonnet-4",
        "max_tokens": 4096,
        "system": "You are a helpful assistant.",
        "tools": [
            {
                "name": "read_file",
                "description": "Read a file",
                "input_schema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            }
        ],
        "tool_choice": {"type": "auto"},
        "messages": [
            {"role": "user", "content": "Read the file /tmp/test.txt"},
        ],
        "stream": True,
    }

    result = anthropic_to_openai_request(anthropic_request)

    assert result["model"] == "claude-sonnet-4"
    assert result["max_tokens"] == 4096
    assert result["stream"] is True
    assert len(result["messages"]) == 2  # system + user
    assert "tools" in result
    assert len(result["tools"]) == 1
    assert result["tools"][0]["type"] == "function"
    assert result["tools"][0]["function"]["name"] == "read_file"
    assert result["tool_choice"] == "auto"

    print("✅ Full request with tools: PASSED")


# ═══════════════════════════════════════════════════════════════════
#  4. tool_use / tool_result message conversion
# ═══════════════════════════════════════════════════════════════════


def test_tool_use_message_conversion():
    """Assistant message with tool_use → OpenAI assistant with tool_calls."""
    anthropic_request = {
        "model": "claude-sonnet-4",
        "max_tokens": 4096,
        "messages": [
            {"role": "user", "content": "Read /tmp/test.txt"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "I'll read that file for you."},
                    {
                        "type": "tool_use",
                        "id": "toolu_abc123",
                        "name": "read_file",
                        "input": {"path": "/tmp/test.txt"},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_abc123",
                        "content": "File content: Hello World",
                    }
                ],
            },
        ],
    }

    result = anthropic_to_openai_request(anthropic_request)

    # Should produce: user, assistant (with tool_calls), tool
    assert len(result["messages"]) == 3

    # Assistant message
    assistant_msg = result["messages"][1]
    assert assistant_msg["role"] == "assistant"
    assert assistant_msg["content"] == "I'll read that file for you."
    assert len(assistant_msg["tool_calls"]) == 1
    assert assistant_msg["tool_calls"][0]["id"] == "toolu_abc123"
    assert assistant_msg["tool_calls"][0]["type"] == "function"
    assert assistant_msg["tool_calls"][0]["function"]["name"] == "read_file"
    args = json.loads(assistant_msg["tool_calls"][0]["function"]["arguments"])
    assert args == {"path": "/tmp/test.txt"}

    # Tool result message
    tool_msg = result["messages"][2]
    assert tool_msg["role"] == "tool"
    assert tool_msg["tool_call_id"] == "toolu_abc123"
    assert tool_msg["content"] == "File content: Hello World"

    print("✅ tool_use/tool_result message conversion: PASSED")


def test_tool_result_with_error():
    """Tool result with is_error flag."""
    anthropic_request = {
        "model": "claude-sonnet-4",
        "max_tokens": 4096,
        "messages": [
            {"role": "user", "content": "Read /nonexistent"},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_err123",
                        "name": "read_file",
                        "input": {"path": "/nonexistent"},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_err123",
                        "content": "File not found",
                        "is_error": True,
                    }
                ],
            },
        ],
    }

    result = anthropic_to_openai_request(anthropic_request)
    tool_msg = result["messages"][2]
    assert tool_msg["role"] == "tool"
    assert "[ERROR]" in tool_msg["content"]

    print("✅ tool_result with error: PASSED")


# ═══════════════════════════════════════════════════════════════════
#  5. Non-streaming response with tool_calls
# ═══════════════════════════════════════════════════════════════════


def test_response_with_tool_calls():
    """OpenAI response with tool_calls → Anthropic tool_use."""
    openai_resp = {
        "id": "chatcmpl-123",
        "choices": [
            {
                "message": {
                    "content": "Let me read that file.",
                    "tool_calls": [
                        {
                            "id": "call_abc123",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": '{"path": "/tmp/test.txt"}',
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50},
    }

    result = openai_to_anthropic_response(openai_resp, "claude-sonnet-4")

    assert result["type"] == "message"
    assert result["role"] == "assistant"
    assert result["stop_reason"] == "tool_use"
    assert len(result["content"]) == 2

    # Text block
    assert result["content"][0]["type"] == "text"
    assert result["content"][0]["text"] == "Let me read that file."

    # Tool use block
    assert result["content"][1]["type"] == "tool_use"
    assert result["content"][1]["id"] == "call_abc123"
    assert result["content"][1]["name"] == "read_file"
    assert result["content"][1]["input"] == {"path": "/tmp/test.txt"}

    print("✅ Non-streaming response with tool_calls: PASSED")


def test_response_tool_calls_only():
    """OpenAI response with tool_calls but no text."""
    openai_resp = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_xyz",
                            "type": "function",
                            "function": {
                                "name": "write_file",
                                "arguments": '{"path": "/tmp/out.txt", "content": "Hello"}',
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {},
    }

    result = openai_to_anthropic_response(openai_resp, "claude-sonnet-4")
    assert result["stop_reason"] == "tool_use"
    assert len(result["content"]) == 1  # Only tool_use, no text
    assert result["content"][0]["type"] == "tool_use"

    print("✅ Response with tool_calls only (no text): PASSED")


# ═══════════════════════════════════════════════════════════════════
#  6. Streaming response with tool_calls
# ═══════════════════════════════════════════════════════════════════


async def _test_streaming_with_tool_calls():
    """OpenAI streaming tool_calls → Anthropic tool_use SSE events."""

    # Simulate OpenAI SSE stream with tool calls
    openai_chunks = [
        # First chunk: text content
        b'data: {"choices":[{"delta":{"role":"assistant","content":""},"index":0}]}\n',
        b'data: {"choices":[{"delta":{"content":"Let me "},"index":0}]}\n',
        b'data: {"choices":[{"delta":{"content":"read that."},"index":0}]}\n',
        # Tool call starts
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_123","type":"function","function":{"name":"read_file","arguments":""}}]},"index":0}]}\n',
        # Tool call arguments stream
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"pa"}}]},"index":0}]}\n',
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"th\\": \\"/tmp"}}]},"index":0}]}\n',
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"/test.txt\\"}"}}]},"index":0}]}\n',
        # Finish
        b'data: {"choices":[{"delta":{},"index":0,"finish_reason":"tool_calls"}]}\n',
        b"data: [DONE]\n",
    ]

    async def mock_stream():
        for chunk in openai_chunks:
            yield chunk

    events = []
    async for event_bytes in openai_stream_to_anthropic_stream(
        mock_stream(), "claude-sonnet-4"
    ):
        text = event_bytes.decode("utf-8").strip()
        for line_pair in text.split("\n\n"):
            lines = line_pair.strip().split("\n")
            if len(lines) >= 2:
                event_type = lines[0].replace("event: ", "")
                data = json.loads(lines[1].replace("data: ", ""))
                events.append((event_type, data))

    # Verify event sequence
    event_types = [e[0] for e in events]

    assert "message_start" in event_types
    assert "content_block_start" in event_types
    assert "content_block_delta" in event_types
    assert "content_block_stop" in event_types
    assert "message_delta" in event_types
    assert "message_stop" in event_types

    # Find tool_use events
    tool_start_events = [
        (t, d) for t, d in events
        if t == "content_block_start" and d.get("content_block", {}).get("type") == "tool_use"
    ]
    assert len(tool_start_events) == 1
    assert tool_start_events[0][1]["content_block"]["name"] == "read_file"
    assert tool_start_events[0][1]["content_block"]["id"] == "call_123"

    # Find input_json_delta events
    json_deltas = [
        (t, d) for t, d in events
        if t == "content_block_delta" and d.get("delta", {}).get("type") == "input_json_delta"
    ]
    assert len(json_deltas) >= 1

    # Reconstruct the arguments
    full_args = "".join(d["delta"]["partial_json"] for _, d in json_deltas)
    parsed = json.loads(full_args)
    assert parsed == {"path": "/tmp/test.txt"}

    # Check stop_reason
    msg_delta = [d for t, d in events if t == "message_delta"][0]
    assert msg_delta["delta"]["stop_reason"] == "tool_use"

    print("✅ Streaming with tool_calls: PASSED")


def test_streaming_with_tool_calls():
    asyncio.run(_test_streaming_with_tool_calls())


# ═══════════════════════════════════════════════════════════════════
#  7. Multiple tool calls in one response
# ═══════════════════════════════════════════════════════════════════


def test_multiple_tool_calls():
    """OpenAI response with multiple tool_calls."""
    openai_resp = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": '{"path": "/a.txt"}',
                            },
                        },
                        {
                            "id": "call_2",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": '{"path": "/b.txt"}',
                            },
                        },
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {},
    }

    result = openai_to_anthropic_response(openai_resp, "claude-sonnet-4")
    assert len(result["content"]) == 2
    assert result["content"][0]["type"] == "tool_use"
    assert result["content"][0]["name"] == "read_file"
    assert result["content"][1]["type"] == "tool_use"
    assert result["content"][1]["input"]["path"] == "/b.txt"

    print("✅ Multiple tool calls: PASSED")


# ═══════════════════════════════════════════════════════════════════
#  8. Copilot split-choices format (text + tool_calls in separate choices)
# ═══════════════════════════════════════════════════════════════════


def test_copilot_split_choices():
    """Copilot backend splits text and tool_calls into separate choices."""
    openai_resp = {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": "I'll use the calculator tool to compute 25*17.",
                    "role": "assistant",
                },
            },
            {
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "function": {
                                "arguments": '{"expr":"25*17"}',
                                "name": "calculator",
                            },
                            "id": "tooluse_8bjtwWDo",
                            "type": "function",
                        }
                    ],
                },
            },
        ],
        "usage": {
            "completion_tokens": 67,
            "prompt_tokens": 378,
            "total_tokens": 445,
        },
        "model": "Claude Sonnet 4",
    }

    result = openai_to_anthropic_response(openai_resp, "claude-sonnet-4")

    assert result["stop_reason"] == "tool_use"
    assert len(result["content"]) == 2

    # Text from choices[0]
    assert result["content"][0]["type"] == "text"
    assert "calculator" in result["content"][0]["text"]

    # tool_use from choices[1]
    assert result["content"][1]["type"] == "tool_use"
    assert result["content"][1]["id"] == "tooluse_8bjtwWDo"
    assert result["content"][1]["name"] == "calculator"
    assert result["content"][1]["input"] == {"expr": "25*17"}

    print("✅ Copilot split-choices format: PASSED")


async def _test_streaming_copilot_split_choices():
    """Copilot streaming: text in choices[0], tool_calls in choices[1]."""
    openai_chunks = [
        # Text in choices[0]
        b'data: {"choices":[{"delta":{"role":"assistant","content":""},"index":0}]}\n',
        b'data: {"choices":[{"delta":{"content":"I\'ll compute that."},"index":0}]}\n',
        # Tool call in choices[1] (separate choice index!)
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"tc_split","type":"function","function":{"name":"calculator","arguments":""}}]},"index":1}]}\n',
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"expr\\":\\"25*17\\"}"}}]},"index":1}]}\n',
        # Finish from choices[1]
        b'data: {"choices":[{"delta":{},"index":1,"finish_reason":"tool_calls"}]}\n',
        b"data: [DONE]\n",
    ]

    async def mock_stream():
        for chunk in openai_chunks:
            yield chunk

    events = []
    async for event_bytes in openai_stream_to_anthropic_stream(
        mock_stream(), "claude-sonnet-4"
    ):
        text = event_bytes.decode("utf-8").strip()
        for line_pair in text.split("\n\n"):
            lines = line_pair.strip().split("\n")
            if len(lines) >= 2:
                event_type = lines[0].replace("event: ", "")
                data = json.loads(lines[1].replace("data: ", ""))
                events.append((event_type, data))

    # Should have both text and tool_use blocks
    tool_starts = [
        d for t, d in events
        if t == "content_block_start" and d.get("content_block", {}).get("type") == "tool_use"
    ]
    assert len(tool_starts) == 1
    assert tool_starts[0]["content_block"]["name"] == "calculator"

    text_deltas = [
        d for t, d in events
        if t == "content_block_delta" and d.get("delta", {}).get("type") == "text_delta"
    ]
    assert len(text_deltas) >= 1

    msg_delta = [d for t, d in events if t == "message_delta"][0]
    assert msg_delta["delta"]["stop_reason"] == "tool_use"

    print("✅ Streaming Copilot split-choices: PASSED")


def test_streaming_copilot_split_choices():
    asyncio.run(_test_streaming_copilot_split_choices())


# ═══════════════════════════════════════════════════════════════════
#  Run all tests
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    test_tools_definition_conversion()
    test_tool_choice_conversion()
    test_full_request_with_tools()
    test_tool_use_message_conversion()
    test_tool_result_with_error()
    test_response_with_tool_calls()
    test_response_tool_calls_only()
    test_streaming_with_tool_calls()
    test_multiple_tool_calls()
    test_copilot_split_choices()
    test_streaming_copilot_split_choices()
    print("\n🎉 All tools translation tests passed!")
