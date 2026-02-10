"""Credential persistence — read/write ~/.copilotx/auth.json."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import asdict, dataclass
from pathlib import Path

from copilotx.config import AUTH_FILE, COPILOTX_DIR


@dataclass
class Credentials:
    """Stored credential pair."""

    github_token: str  # long-lived GitHub OAuth token
    copilot_token: str = ""  # short-lived Copilot JWT
    expires_at: float = 0.0  # unix timestamp of Copilot JWT expiry
    api_base_url: str = ""  # dynamic API base from endpoints.api


class AuthStorage:
    """Manages credential persistence on disk."""

    def __init__(self, path: Path = AUTH_FILE) -> None:
        self.path = path

    # ── public ──────────────────────────────────────────────────────

    def load(self) -> Credentials | None:
        """Load credentials from disk.  Returns None if not found."""
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return Credentials(
                github_token=data["github_token"],
                copilot_token=data.get("copilot_token", ""),
                expires_at=data.get("expires_at", 0.0),
                api_base_url=data.get("api_base_url", ""),
            )
        except (json.JSONDecodeError, KeyError):
            return None

    def save(self, creds: Credentials) -> None:
        """Write credentials to disk with restricted permissions (owner-only)."""
        self._ensure_dir()
        self.path.write_text(
            json.dumps(asdict(creds), indent=2) + "\n",
            encoding="utf-8",
        )
        # chmod 600 — owner read/write only (skip on Windows)
        if os.name != "nt":
            self.path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    def delete(self) -> bool:
        """Remove stored credentials.  Returns True if file existed."""
        if self.path.exists():
            self.path.unlink()
            return True
        return False

    def exists(self) -> bool:
        return self.path.exists() and self.load() is not None

    # ── private ─────────────────────────────────────────────────────

    def _ensure_dir(self) -> None:
        COPILOTX_DIR.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            COPILOTX_DIR.chmod(stat.S_IRWXU)
