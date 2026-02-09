# 🚀 CopilotX

Local GitHub Copilot API proxy — use GPT-4o, Claude, Gemini and more via OpenAI/Anthropic compatible APIs.

Turn your GitHub Copilot subscription into a local AI API server. Use **any model** available through Copilot with **any tool** that supports OpenAI or Anthropic SDKs.

## ✨ Features

- 🔐 **GitHub OAuth** — One-command login via Device Flow, or use existing token
- 🔄 **Auto Token Refresh** — Copilot JWT refreshed transparently before expiry
- 🔌 **Dual API Format** — OpenAI `/v1/chat/completions` + Anthropic `/v1/messages`
- 🌊 **SSE Streaming** — Real-time streaming responses for both formats
- 📋 **Model Discovery** — Auto-fetch available models from Copilot
- ⚡ **Zero Config** — `pip install` → `auth login` → `serve` → done

## 🚀 Quick Start

### 1. Install

```bash
pip install copilotx
# or
uv pip install copilotx
```

### 2. Authenticate

```bash
# Option A: OAuth Device Flow (recommended)
copilotx auth login
# → Opens browser for GitHub authorization

# Option B: Use existing GitHub token
copilotx auth login --token ghp_xxxxx
# or
export GITHUB_TOKEN=ghp_xxxxx && copilotx auth login
```

### 3. Start Server

```bash
copilotx serve
```

Output:
```
🚀 CopilotX v1.0.0
✅ Copilot Token valid (28m remaining, auto-refresh)
📋 Models: gpt-4o, gpt-4o-mini, o3-mini, claude-sonnet-4, gemini-2.0-flash

🔗 OpenAI API:    http://127.0.0.1:24680/v1/chat/completions
🔗 Anthropic API: http://127.0.0.1:24680/v1/messages
🔗 Models:        http://127.0.0.1:24680/v1/models

Press Ctrl+C to stop
```

### 4. Use It

**Python (OpenAI SDK):**

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:24680/v1", api_key="copilotx")

response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello!"}],
    stream=True,
)

for chunk in response:
    print(chunk.choices[0].delta.content or "", end="")
```

**Python (Anthropic SDK):**

```python
from anthropic import Anthropic

client = Anthropic(base_url="http://localhost:24680", api_key="copilotx")

message = client.messages.create(
    model="claude-sonnet-4",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Hello!"}],
)
print(message.content[0].text)
```

**Claude Code:**

```bash
# Set environment variables
export ANTHROPIC_BASE_URL=http://localhost:24680
export ANTHROPIC_API_KEY=copilotx
claude
```

**Codex:**

```bash
export OPENAI_BASE_URL=http://localhost:24680/v1
export OPENAI_API_KEY=copilotx
codex
```

**cURL:**

```bash
curl http://localhost:24680/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

## 📡 API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/chat/completions` | POST | OpenAI-compatible chat completions |
| `/v1/messages` | POST | Anthropic-compatible messages |
| `/v1/models` | GET | List available models |
| `/health` | GET | Server health + token status |

## 🔧 CLI Commands

```bash
copilotx auth login              # OAuth Device Flow login
copilotx auth login --token XXX  # Quick login with existing token
copilotx auth status             # Show auth status
copilotx auth logout             # Clear credentials

copilotx models                  # List available models
copilotx serve                   # Start server (default: 127.0.0.1:24680)
copilotx serve --port 9090       # Custom port (strict — fails if in use)
copilotx --version               # Show version
```

## 🏗️ How It Works

```
Your Tool (Claude Code / Codex / Python script)
    │
    │  OpenAI or Anthropic format
    ▼
┌──────────────────────────────┐
│  CopilotX (localhost:24680)  │
│                              │
│  • Anthropic → OpenAI        │
│    format translation        │
│  • Token auto-refresh        │
│  • SSE stream forwarding     │
└──────────────┬───────────────┘
               │  OpenAI format
               ▼
  api.githubcopilot.com/chat/completions
  (GPT-4o, Claude, Gemini, o3-mini, ...)
```

CopilotX uses your GitHub Copilot subscription to access models. The Copilot backend
natively speaks OpenAI format, so OpenAI requests are **direct passthrough**. Anthropic
requests are translated on-the-fly.

## 🔍 Port Discovery

When CopilotX starts, it writes `~/.copilotx/server.json`:

```json
{
  "host": "127.0.0.1",
  "port": 24680,
  "pid": 12345,
  "started_at": "2026-02-09T12:00:00+00:00",
  "base_url": "http://127.0.0.1:24680"
}
```

Other scripts can read this to discover the actual port:

```bash
# Bash/Zsh
PORT=$(python -c "import json; print(json.load(open('$HOME/.copilotx/server.json'))['port'])")
curl http://localhost:$PORT/health

# PowerShell
$info = Get-Content "$HOME\.copilotx\server.json" | ConvertFrom-Json
curl http://localhost:$($info.port)/health
```

The file is automatically cleaned up when the server stops.

## ⚠️ Disclaimer

This tool is for **personal local use only**. Please comply with
[GitHub Copilot Terms of Service](https://docs.github.com/en/copilot/overview-of-github-copilot/about-github-copilot-individual).
The author is not responsible for any account restrictions resulting from misuse.

## 📄 License

MIT
