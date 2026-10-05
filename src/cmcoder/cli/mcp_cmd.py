"""`cmcoder mcp ...`: list, add, remove and approve MCP servers."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console

from ..config.settings import (
    McpServerConfig,
    SettingsError,
    approve,
    config_fingerprint,
    find_project_root,
    is_approved,
    load_settings,
    update_user_settings,
)
from ..mcp_client import McpManager, status_lines

console = Console(highlight=False)
err = Console(stderr=True, highlight=False)

mcp_app = typer.Typer(
    help="Manage MCP servers (tools from other programs and services).",
    add_completion=False,
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _settings() -> Any:
    try:
        return load_settings(Path.cwd())
    except SettingsError as e:
        err.print(f"[red]error:[/red] {e}")
        raise typer.Exit(2) from e


def _pairs(values: list[str] | None, sep: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for v in values or []:
        key, ok, value = v.partition(sep)
        if not ok or not key.strip():
            err.print(f"[red]error:[/red] expected NAME{sep}VALUE, got {v!r}")
            raise typer.Exit(2)
        out[key.strip()] = value.strip()
    return out


@mcp_app.command("list", help="Show configured servers; --check starts them and lists their tools.")
def list_servers(
    check: Annotated[
        bool, typer.Option("--check", help="Start the servers and show their tools.")
    ] = False,
) -> None:
    s = _settings()
    root = find_project_root(Path.cwd().resolve())
    if not s.mcp_servers and not s.project_mcp_servers:
        console.print("No MCP servers. Add one with `cmcoder mcp add`.")
    for name, cfg in s.mcp_servers.items():
        console.print(f"[bold]{name}[/bold]  {cfg.describe()}  [dim](your settings)[/dim]")
    for name, cfg in s.project_mcp_servers.items():
        state = (
            "approved"
            if is_approved(root, f"mcp:{name}", config_fingerprint(cfg))
            else "not approved yet"
        )
        console.print(f"[bold]{name}[/bold]  {cfg.describe()}  [dim](this project; {state})[/dim]")
    for item in s.ignored_project_settings:
        if "mcpServers" in item:
            console.print(
                f"[yellow]ignored[/yellow] {item}: this project isn't trusted (`cmcoder trust`)"
            )
    if check:

        async def go() -> None:
            manager = McpManager(s, root)
            async for warning in manager.start(None):
                err.print(f"[yellow]{warning.message}[/yellow]")
            for line in status_lines(manager):
                console.print(line)
            await manager.close()

        asyncio.run(go())


@mcp_app.command(
    "add",
    help="Add a server to your user settings. Local: cmcoder mcp add NAME -- COMMAND [ARGS...]. "
    "Remote: cmcoder mcp add NAME --url URL.",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def add(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Server name (tools become mcp__NAME__tool).")],
    url: Annotated[str | None, typer.Option("--url", help="Remote server URL.")] = None,
    transport: Annotated[
        str, typer.Option("--transport", help="http (default) or sse, with --url.")
    ] = "http",
    header: Annotated[
        list[str] | None, typer.Option("--header", "-H", help='"Name: value" (repeatable).')
    ] = None,
    env: Annotated[
        list[str] | None, typer.Option("--env", "-e", help="NAME=value (repeatable).")
    ] = None,
) -> None:
    command = list(ctx.args)
    if bool(url) == bool(command):
        err.print("[red]error:[/red] give either --url URL or `-- COMMAND [ARGS...]`")
        raise typer.Exit(2)
    data: dict[str, Any] = (
        {"type": transport, "url": url, "headers": _pairs(header, ":")}
        if url
        else {"command": command[0], "args": command[1:], "env": _pairs(env, "=")}
    )
    data = {k: v for k, v in data.items() if v not in ({}, [], None)}
    McpServerConfig.model_validate(data)  # fails early on a bad combination
    path = update_user_settings(lambda d: d.setdefault("mcpServers", {}).__setitem__(name, data))
    console.print(
        f"Added MCP server [bold]{name}[/bold] to {path}. Check it with `cmcoder mcp list --check`."
    )
    if any("$" not in v for v in data.get("headers", {}).values()) or any(
        "$" not in v for v in data.get("env", {}).values()
    ):
        console.print(
            '[dim]Tip: keep tokens out of the file with ${VAR}, e.g. -H "Authorization: Bearer ${GITHUB_TOKEN}".[/dim]'
        )


@mcp_app.command("remove", help="Remove a server from your user settings.")
def remove(name: str) -> None:
    found: list[bool] = []

    def change(d: dict[str, Any]) -> None:
        servers = d.get("mcpServers") or {}
        found.append(servers.pop(name, None) is not None)

    path = update_user_settings(change)
    if not found[0]:
        err.print(f"[red]error:[/red] no server {name!r} in {path}")
        raise typer.Exit(1)
    console.print(f"Removed {name} from {path}.")


@mcp_app.command("approve", help="Approve a server from this (trusted) project's .mcp.json.")
def approve_server(name: str) -> None:
    s = _settings()
    cfg = s.project_mcp_servers.get(name)
    if cfg is None and name in s.mcp_servers:
        # Added with `cmcoder mcp add`: yours, so nothing to approve.
        blocked = name in s.denied_mcp_servers or (
            s.allowed_mcp_servers is not None and name not in s.allowed_mcp_servers
        )
        if blocked:
            err.print(
                f"[red]error:[/red] {name} is in your settings, but your organisation's managed "
                "settings don't allow it (allowedMcpServers / deniedMcpServers)."
            )
            raise typer.Exit(1)
        console.print(
            f"{name} is in your own settings (added with `cmcoder mcp add`), so it needs no "
            "approval: it starts in every session. `approve` is only for servers in a "
            "project's .mcp.json.\nCheck that it starts and see its tools: "
            "cmcoder mcp list --check"
        )
        return
    if cfg is None:
        hint = (
            " (the project isn't trusted: run `cmcoder trust` first)"
            if not s.project_trusted
            else ""
        )
        err.print(f"[red]error:[/red] this project has no MCP server {name!r}{hint}")
        raise typer.Exit(1)
    for key, value in cfg.approval_details().items():
        console.print(f"{name} {key}: {value}", markup=False, highlight=False)
    root = find_project_root(Path.cwd().resolve())
    approve(root, f"mcp:{name}", config_fingerprint(cfg))
    console.print(f"Approved {name} for this project (until its settings change).")
