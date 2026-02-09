"""Global configuration constants."""

import os
from pathlib import Path

# ── Copilot OAuth ──────────────────────────────────────────────────
# This is the same client_id used by the official VS Code Copilot Chat extension.
GITHUB_CLIENT_ID = "Iv1.b507a08c87ecfe98"
GITHUB_SCOPE = "read:user"
GITHUB_DEVICE_CODE_URL = "https://github.com/login/device/code"
GITHUB_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_COPILOT_TOKEN_URL = "https://api.github.com/copilot_internal/v2/token"

# ── Copilot API ────────────────────────────────────────────────────
COPILOT_API_BASE = "https://api.githubcopilot.com"
COPILOT_CHAT_COMPLETIONS = f"{COPILOT_API_BASE}/chat/completions"
COPILOT_MODELS = f"{COPILOT_API_BASE}/models"

# Headers to mimic the official VS Code Copilot extension
COPILOT_HEADERS = {
    "Editor-Version": "vscode/1.104.3",
    "Editor-Plugin-Version": "copilot-chat/0.26.7",
    "User-Agent": "GitHubCopilotChat/0.26.7",
    "Copilot-Integration-Id": "vscode-chat",
    "X-GitHub-Api-Version": "2025-04-01",
}

# ── Server ─────────────────────────────────────────────────────────
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 24680
REQUEST_TIMEOUT = 120  # seconds

# ── Security ───────────────────────────────────────────────────────
# Set COPILOTX_API_KEY env var to enable API key protection.
# When set: localhost is exempt, remote requests require Bearer token.
# When unset: all requests are allowed (backward compatible).
COPILOTX_API_KEY = os.environ.get("COPILOTX_API_KEY", "")
LOCALHOST_ADDRS = {"127.0.0.1", "::1", "localhost"}
# Paths that are always accessible without API key (health checks, etc.)
PUBLIC_PATHS = {"/health", "/"}

# ── Token ──────────────────────────────────────────────────────────
TOKEN_REFRESH_BUFFER = 60  # refresh token 60s before expiry
DEVICE_CODE_POLL_INTERVAL = 5  # seconds
DEVICE_CODE_TIMEOUT = 900  # 15 minutes

# ── Models Cache ───────────────────────────────────────────────────
MODELS_CACHE_TTL = 300  # 5 minutes

# ── Storage ────────────────────────────────────────────────────────
COPILOTX_DIR = Path.home() / ".copilotx"
AUTH_FILE = COPILOTX_DIR / "auth.json"
SERVER_FILE = COPILOTX_DIR / "server.json"
