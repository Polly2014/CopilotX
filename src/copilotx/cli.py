"""CopilotX CLI — powered by Typer."""

from __future__ import annotations

import asyncio
import os
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from copilotx import __version__

app = typer.Typer(
    name="copilotx",
    help="🚀 CopilotX — Local GitHub Copilot API proxy",
    no_args_is_help=True,
    invoke_without_command=True,
    rich_markup_mode="rich",
)
auth_app = typer.Typer(help="🔐 Authentication management")
app.add_typer(auth_app, name="auth")

console = Console()


# ── Auth commands ───────────────────────────────────────────────────


@auth_app.command("login")
def auth_login(
    token: Optional[str] = typer.Option(
        None,
        "--token",
        "-t",
        help="GitHub token (skip OAuth flow). Can also set GITHUB_TOKEN env var.",
    ),
) -> None:
    """Authenticate with GitHub Copilot."""
    import os

    from copilotx.auth.oauth import OAuthError, device_flow_login
    from copilotx.auth.token import TokenError, TokenManager

    tm = TokenManager()

    # Determine GitHub token source
    github_token = token or os.environ.get("GITHUB_TOKEN")

    if github_token:
        console.print("[dim]Using provided GitHub token...[/]")
    else:
        # Full OAuth Device Flow
        try:
            github_token = asyncio.run(device_flow_login())
        except OAuthError as e:
            console.print(f"[bold red]❌ OAuth failed:[/] {e}")
            raise typer.Exit(1)
        except Exception as e:
            console.print(f"[bold red]❌ Unexpected error:[/] {e}")
            raise typer.Exit(1)

    # Save the GitHub token
    tm.save_github_token(github_token)

    # Verify by fetching a Copilot token
    try:
        asyncio.run(tm.ensure_copilot_token())
    except TokenError as e:
        console.print(f"[bold red]❌ Copilot token exchange failed:[/] {e}")
        raise typer.Exit(1)

    console.print()
    console.print("[bold green]✅ Successfully authenticated with GitHub Copilot![/]")
    console.print(f"[dim]   Credentials saved to {tm.storage.path}[/]")
    console.print(f"[dim]   Copilot token expires in {tm.expires_in_seconds // 60} minutes[/]")


@auth_app.command("status")
def auth_status() -> None:
    """Show current authentication status."""
    from copilotx.auth.token import TokenManager

    tm = TokenManager()
    status = tm.get_status()

    if not status["authenticated"]:
        console.print("[bold red]❌ Not authenticated[/]")
        console.print("[dim]   Run: copilotx auth login[/]")
        raise typer.Exit(1)

    console.print("[bold green]✅ Authenticated[/]")

    if status["copilot_token_valid"]:
        mins = status["expires_in"] // 60
        secs = status["expires_in"] % 60
        console.print(f"[dim]   Copilot token valid ({mins}m {secs}s remaining)[/]")
    else:
        console.print("[yellow]   Copilot token expired (will auto-refresh on next request)[/]")


@auth_app.command("logout")
def auth_logout() -> None:
    """Remove stored credentials."""
    from copilotx.auth.token import TokenManager

    tm = TokenManager()
    if tm.logout():
        console.print("[bold green]✅ Credentials removed[/]")
    else:
        console.print("[dim]No credentials found[/]")


# ── Models command ──────────────────────────────────────────────────


@app.command("models")
def list_models() -> None:
    """List available Copilot models."""
    from copilotx.auth.token import TokenError, TokenManager
    from copilotx.proxy.client import CopilotClient

    tm = TokenManager()
    if not tm.is_authenticated:
        console.print("[bold red]❌ Not authenticated. Run: copilotx auth login[/]")
        raise typer.Exit(1)

    async def _fetch():
        token = await tm.ensure_copilot_token()
        async with CopilotClient(token) as client:
            return await client.list_models()

    try:
        models = asyncio.run(_fetch())
    except TokenError as e:
        console.print(f"[bold red]❌ {e}[/]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[bold red]❌ Failed to fetch models:[/] {e}")
        raise typer.Exit(1)

    if not models:
        console.print("[yellow]No models available[/]")
        return

    table = Table(title="📋 Available Models", show_lines=False)
    table.add_column("Model ID", style="cyan", no_wrap=True)
    table.add_column("Name", style="white")
    table.add_column("Vendor", style="dim")

    for m in models:
        table.add_row(m["id"], m.get("name", "—"), m.get("vendor", "—"))

    console.print(table)
    console.print(f"\n[dim]Total: {len(models)} models[/]")


# ── Serve command ───────────────────────────────────────────────────


@app.command("serve")
def serve(
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="Bind address"),
    port: int = typer.Option(24680, "--port", "-p", help="Bind port (default: 24680)"),
    port_explicit: bool = typer.Option(False, hidden=True),
) -> None:
    """Start the local API proxy server."""
    import json
    import os
    import signal
    import socket
    import sys

    from copilotx.auth.token import TokenError, TokenManager
    from copilotx.config import COPILOTX_DIR, DEFAULT_PORT, SERVER_FILE
    from copilotx.proxy.client import CopilotClient

    tm = TokenManager()
    if not tm.is_authenticated:
        console.print("[bold red]❌ Not authenticated. Run: copilotx auth login[/]")
        raise typer.Exit(1)

    # Pre-validate token
    try:
        asyncio.run(tm.ensure_copilot_token())
    except TokenError as e:
        console.print(f"[bold red]❌ {e}[/]")
        raise typer.Exit(1)

    # Detect if --port was explicitly passed via sys.argv
    _port_was_explicit = any(
        arg in sys.argv for arg in ("--port", "-p")
    )

    if _port_was_explicit:
        # Strict mode: user chose this port, fail if unavailable
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind((host, port))
        except OSError:
            console.print(
                f"[bold red]❌ Port {port} is already in use.[/]\n"
                f"[dim]   Free it or omit --port to auto-select.[/]"
            )
            raise typer.Exit(1)
    else:
        # Auto mode: scan for available port
        actual_port = _find_available_port(host, port)
        if actual_port != port:
            console.print(
                f"[yellow]⚠️  Port {port} is in use, using {actual_port} instead[/]"
            )
        port = actual_port

    # Fetch models for display
    try:

        async def _fetch():
            token = await tm.ensure_copilot_token()
            async with CopilotClient(token) as client:
                return await client.list_models()

        models = asyncio.run(_fetch())
        model_names = [m["id"] for m in models]
    except Exception:
        model_names = ["(could not fetch)"]

    # Write server.json for port discovery
    _write_server_info(host, port)

    # Detect mode
    is_remote = host != "127.0.0.1"
    has_api_key = bool(os.environ.get("COPILOTX_API_KEY", ""))

    # Banner
    console.print()
    console.print(f"[bold cyan]🚀 CopilotX v{__version__}[/]")
    console.print(
        f"[green]✅ Copilot Token valid "
        f"({tm.expires_in_seconds // 60}m remaining, auto-refresh)[/]"
    )

    if is_remote:
        if has_api_key:
            console.print("[green]🔐 API Key protection: ON (localhost exempt)[/]")
        else:
            console.print(
                "[bold yellow]⚠️  WARNING: Remote mode without API key![/]\n"
                "[yellow]   Anyone can access your Copilot subscription.[/]\n"
                "[yellow]   Set COPILOTX_API_KEY env var to enable protection.[/]"
            )
    else:
        console.print("[dim]🏠 Local mode (localhost only)[/]")

    # Show dynamic API base URL
    api_base = tm.api_base_url
    if api_base:
        # Extract hostname for display
        from urllib.parse import urlparse
        api_host = urlparse(api_base).hostname or api_base
        console.print(f"[dim]🎯 API: {api_host} (auto-detected)[/]")

    console.print(f"[dim]📋 Models: {', '.join(model_names)}[/]")
    console.print(f"[dim]📁 Port info: {SERVER_FILE}[/]")
    console.print()
    console.print(f"[bold]🔗 OpenAI Chat:[/]   http://{host}:{port}/v1/chat/completions")
    console.print(f"[bold]🔗 Responses:[/]     http://{host}:{port}/v1/responses")
    console.print(f"[bold]🔗 Anthropic API:[/] http://{host}:{port}/v1/messages")
    console.print(f"[bold]🔗 Models:[/]        http://{host}:{port}/v1/models")
    console.print()
    console.print("[dim]Press Ctrl+C to stop[/]")
    console.print()

    # Start server (cleanup server.json on exit)
    import uvicorn

    from copilotx.server.app import create_app

    fastapi_app = create_app(tm)
    try:
        uvicorn.run(fastapi_app, host=host, port=port, log_level="info")
    finally:
        _cleanup_server_info()


def _write_server_info(host: str, port: int) -> None:
    """Write server.json so other tools can discover the running port."""
    import json
    import os
    from datetime import datetime, timezone

    from copilotx.config import COPILOTX_DIR, SERVER_FILE

    COPILOTX_DIR.mkdir(parents=True, exist_ok=True)
    info = {
        "host": host,
        "port": port,
        "pid": os.getpid(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "base_url": f"http://{host}:{port}",
    }
    SERVER_FILE.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")


def _cleanup_server_info() -> None:
    """Remove server.json on shutdown."""
    from copilotx.config import SERVER_FILE

    try:
        SERVER_FILE.unlink(missing_ok=True)
    except Exception:
        pass  # best-effort


def _find_available_port(host: str, preferred: int, max_attempts: int = 20) -> int:
    """Find an available port starting from preferred, trying sequentially."""
    import socket

    for offset in range(max_attempts):
        port = preferred + offset
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind((host, port))
                return port
        except OSError:
            continue
    # Fallback: let OS pick a random port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


# ── Version ─────────────────────────────────────────────────────────


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False, "--version", "-v", help="Show version and exit"
    ),
) -> None:
    if version:
        console.print(f"CopilotX v{__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None and not version:
        # No command given, show help
        console.print(ctx.get_help())
        raise typer.Exit()
