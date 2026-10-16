"""MCP client: tools (and resources, prompts) from MCP servers.

Servers come from your user settings (`mcpServers`), the managed settings,
and a trusted project's `.mcp.json`; each project server is approved once
before it first starts. They start at the beginning of the first turn, each
in its own task (the SDK's connections must be opened and closed in the same
task), and stop when the agent closes. A server that fails to start is
reported and skipped; cmcoder carries on without it.

Tools appear to the model as `mcp__<server>__<tool>` and go through the
permission engine like any other tool (they ask unless an allow rule such as
`mcp__github` or `mcp__github__create_issue` says otherwise).

Each server is reached through FastMCP's client (on the `mcp` library 2.x),
the client LangChain's own MCP support (`langchain.mcp`) runs on, so the
Phase 8 engine's tools use the same connection (engine/mcp.py, design
decision D18).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import warnings
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import ConfigDict

from .compat import IS_WINDOWS, find_program
from .config.settings import (
    McpServerConfig,
    Settings,
    approve,
    config_dir,
    config_fingerprint,
    is_approved,
)
from .protocol import events as ev
from .providers.messages import ToolSpec
from .providers.transport import TransportOptions, build_ssl_context
from .tools.base import Tool, ToolContext, ToolInput, ToolResult

MAX_DESCRIPTION_CHARS = 2000
NAME_RE = re.compile(r"[^A-Za-z0-9_-]")
VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

Origin = Literal["user", "project"]
Status = Literal["starting", "connected", "failed", "disabled", "not approved", "blocked"]


def tool_name(server: str, tool: str) -> str:
    """`mcp__<server>__<tool>`, with characters model APIs reject replaced, max 64."""
    return f"mcp__{NAME_RE.sub('_', server)}__{NAME_RE.sub('_', tool)}"[:64]


def expand(value: str, environ: dict[str, str] | None = None) -> str:
    """`${VAR}` and `${VAR:-default}` from the environment. A missing variable
    without a default is an error (better than connecting with an empty token)."""
    env = os.environ if environ is None else environ

    def repl(m: re.Match[str]) -> str:
        name, default = m.group(1), m.group(2)
        if name in env:
            return env[name]
        if default is not None:
            return default
        raise ValueError(f"environment variable {name} is not set")

    return VAR_RE.sub(repl, value)


def resolve_command(command: str) -> str:
    """The server program's full path, never one planted in the project
    (Windows looks in the current folder first; see compat.find_program)."""
    if os.path.isabs(command) or "/" in command or "\\" in command:
        return command
    found = find_program(command)
    if found is None and IS_WINDOWS:
        for ext in (".cmd", ".bat", ".exe"):  # e.g. npx is npx.cmd
            if found := find_program(command + ext):
                break
    if found is None:
        raise FileNotFoundError(f"{command!r} was not found on PATH")
    return found


class McpArgs(ToolInput):
    """Arguments are checked by the server against its own schema."""

    model_config = ConfigDict(extra="allow")


@dataclass
class McpServer:
    name: str
    config: McpServerConfig
    origin: Origin
    status: Status = "starting"
    error: str = ""
    tools: list[Any] = field(default_factory=list)  # mcp Tool objects
    resources: bool = False
    prompts: list[Any] = field(default_factory=list)
    client: Any = None
    _task: asyncio.Task[None] | None = None
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    _ready: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def key(self) -> str:
        return f"mcp:{self.name}"

    def summary(self) -> str:
        detail = self.error or (f"{len(self.tools)} tools" if self.status == "connected" else "")
        origin = " (project)" if self.origin == "project" else ""
        return f"{self.name}{origin}: {self.status}" + (f" · {detail}" if detail else "")


class McpTool(Tool):
    """One tool of an MCP server, as a cmcoder tool."""

    read_only: ClassVar[bool] = False
    Input = McpArgs

    def __init__(self, server: McpServer, tool: Any) -> None:
        self.server = server
        self.tool = tool
        self.name = tool_name(server.name, tool.name)  # type: ignore[misc]
        text = (tool.description or tool.title or tool.name).strip()
        self.description = f"[MCP server {server.name}] {text}"[:MAX_DESCRIPTION_CHARS]  # type: ignore[misc]

    def spec(self) -> ToolSpec:
        schema = dict(self.tool.input_schema or {})
        schema.setdefault("type", "object")
        schema.setdefault("properties", {})
        return ToolSpec(name=self.name, description=self.description, parameters=schema)

    def describe(self, args: McpArgs, ctx: ToolContext) -> str:
        brief = json.dumps(args.model_dump(), ensure_ascii=False)
        return f"{self.name}({brief if len(brief) <= 60 else brief[:57] + '...'})"

    async def run(self, args: McpArgs, ctx: ToolContext) -> ToolResult:
        if self.server.status != "connected":
            return ToolResult(
                f"MCP server {self.server.name} is {self.server.status}.", is_error=True
            )
        try:
            result = await asyncio.wait_for(
                self.server.client.call_tool_mcp(self.tool.name, args.model_dump()),
                self.server.config.tool_timeout,
            )
        except TimeoutError:
            return ToolResult(
                f"{self.name} timed out after {self.server.config.tool_timeout:.0f}s.",
                is_error=True,
            )
        except Exception as e:  # the server's problem, not the agent's
            return ToolResult(f"{self.name} failed: {type(e).__name__}: {e}", is_error=True)
        return ToolResult(content_text(result), is_error=bool(result.is_error))


def content_text(result: Any) -> str:
    """A tool result or a resource's contents (a list) as text for the model."""
    parts: list[str] = []
    items = result if isinstance(result, list) else getattr(result, "content", None) or []
    for item in items:
        kind = getattr(item, "type", "")
        if hasattr(item, "text") and item.text is not None:
            parts.append(item.text)
        elif kind in ("image", "audio"):
            parts.append(f"[{kind}: {getattr(item, 'mime_type', '?')}; not shown to the model]")
        elif kind == "resource_link":
            parts.append(f"[resource: {item.uri}]")
        elif kind == "resource" and getattr(item, "resource", None) is not None:
            res = item.resource
            parts.append(getattr(res, "text", None) or f"[resource: {res.uri}]")
        elif getattr(item, "blob", None) is not None:
            parts.append(f"[binary content: {getattr(item, 'mime_type', '?')}]")
    structured = None if isinstance(result, list) else getattr(result, "structured_content", None)
    if not parts and structured is not None:
        parts.append(json.dumps(structured, ensure_ascii=False, indent=2))
    return "\n".join(parts) or "(no content)"


class ListMcpResourcesInput(ToolInput):
    server: str | None = None


class ListMcpResourcesTool(Tool):
    name = "ListMcpResources"
    description = (
        "List the resources (documents, data) MCP servers offer, optionally for one server. "
        "Read one with ReadMcpResource."
    )
    Input = ListMcpResourcesInput
    read_only = True

    def __init__(self, manager: McpManager) -> None:
        self.manager = manager

    async def run(self, args: ListMcpResourcesInput, ctx: ToolContext) -> ToolResult:
        lines: list[str] = []
        for s in self.manager.connected():
            if not s.resources or (args.server and s.name != args.server):
                continue
            try:
                listed = await asyncio.wait_for(s.client.list_resources(), s.config.tool_timeout)
            except Exception as e:
                lines.append(f"{s.name}: failed to list resources ({e})")
                continue
            for r in listed:
                lines.append(
                    f"{s.name}: {r.uri} — {r.name}"
                    + (f": {r.description}" if r.description else "")
                )
        return ToolResult("\n".join(lines) or "No MCP resources.")


class ReadMcpResourceInput(ToolInput):
    server: str
    uri: str


class ReadMcpResourceTool(Tool):
    name = "ReadMcpResource"
    description = "Read a resource from an MCP server by its URI (see ListMcpResources)."
    Input = ReadMcpResourceInput
    read_only = True

    def __init__(self, manager: McpManager) -> None:
        self.manager = manager

    def describe(self, args: ReadMcpResourceInput, ctx: ToolContext) -> str:
        return f"ReadMcpResource({args.server}: {args.uri})"

    async def run(self, args: ReadMcpResourceInput, ctx: ToolContext) -> ToolResult:
        server = next((s for s in self.manager.connected() if s.name == args.server), None)
        if server is None:
            return ToolResult(f"No connected MCP server named {args.server!r}.", is_error=True)
        try:
            result = await asyncio.wait_for(
                server.client.read_resource(args.uri), server.config.tool_timeout
            )
        except Exception as e:
            return ToolResult(f"Reading {args.uri} failed: {e}", is_error=True)
        return ToolResult(content_text(result))


# Asks the user to approve a project's server: (server) -> allowed?
ApproveFn = Callable[[McpServer], Awaitable[bool]]


class McpManager:
    def __init__(self, settings: Settings, project_root: Path) -> None:
        self.root = project_root
        allowed = settings.allowed_mcp_servers
        denied = set(settings.denied_mcp_servers)
        self.servers: list[McpServer] = []
        for origin, configs in (
            ("user", settings.mcp_servers),
            ("project", settings.project_mcp_servers),
        ):
            for name, cfg in configs.items():
                if any(s.name == name for s in self.servers):
                    continue  # your own definition wins over a project's
                server = McpServer(name, cfg, origin)  # type: ignore[arg-type]
                if cfg.disabled:
                    server.status = "disabled"
                elif name in denied or (allowed is not None and name not in allowed):
                    server.status, server.error = "blocked", "not allowed by your organisation"
                self.servers.append(server)
        self.started = False

    def connected(self) -> list[McpServer]:
        return [s for s in self.servers if s.status == "connected"]

    def tools(self) -> list[Tool]:
        out: list[Tool] = []
        for s in self.connected():
            out += [McpTool(s, t) for t in s.tools]
        if any(s.resources for s in self.connected()):
            out += [ListMcpResourcesTool(self), ReadMcpResourceTool(self)]
        return out

    async def start(self, approve_fn: ApproveFn | None) -> AsyncIterator[ev.Warning]:
        """Start every server (once); yields a warning for each that can't start."""
        if self.started:
            return
        self.started = True
        pending: list[McpServer] = []
        for s in self.servers:
            if s.status != "starting":
                continue
            if s.origin == "project":
                fp = config_fingerprint(s.config)
                if not is_approved(self.root, s.key, fp):
                    ok = approve_fn is not None and await approve_fn(s)
                    if not ok:
                        s.status = "not approved"
                        s.error = "approve it with `cmcoder mcp approve " + s.name + "`"
                        continue
                    approve(self.root, s.key, fp)
            s._task = asyncio.create_task(self._run(s), name=f"mcp:{s.name}")
            pending.append(s)
        if pending:
            await asyncio.wait(
                [asyncio.create_task(s._ready.wait()) for s in pending],
                timeout=max(s.config.startup_timeout for s in pending) + 1,
            )
        for s in pending:
            if s.status == "starting":
                s.status, s.error = "failed", "timed out while starting"
            if s.status == "failed":
                yield ev.Warning(message=f"MCP server {s.name} could not start: {s.error}")

    async def _run(self, s: McpServer) -> None:
        """Owns one server's connection for its whole life."""
        Client = fastmcp_client()
        try:
            client = Client(
                self._transport(s),
                timeout=s.config.tool_timeout,
                init_timeout=s.config.startup_timeout,
            )
            async with client:
                s.client = client
                listed = await asyncio.wait_for(client.list_tools(), s.config.startup_timeout)
                s.tools = list(listed)
                caps = client.server_capabilities
                s.resources = caps is not None and caps.resources is not None
                if caps is not None and caps.prompts is not None:
                    with suppress(Exception):
                        s.prompts = list(await client.list_prompts())
                s.status = "connected"
                s._ready.set()
                await s._stop.wait()
        except asyncio.CancelledError:
            raise
        except BaseException as e:  # noqa: BLE001 - anyio groups raise ExceptionGroups
            s.status, s.error = "failed", _describe(e)
        finally:
            s.client = None
            if s.status == "connected":
                s.status = "disabled"
            s._ready.set()

    def _transport(self, s: McpServer) -> Any:
        import httpx2
        from fastmcp.client.transports import SSETransport, StdioTransport, StreamableHttpTransport

        cfg = s.config
        if cfg.transport == "stdio":
            assert cfg.command
            log_dir = config_dir() / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            return StdioTransport(
                command=resolve_command(expand(cfg.command)),
                args=[expand(a) for a in cfg.args],
                env={k: expand(v) for k, v in cfg.env.items()},
                cwd=expand(cfg.cwd) if cfg.cwd else str(self.root),
                # The server's stderr, one file per server (`/mcp` points to it).
                log_file=log_dir / f"mcp-{NAME_RE.sub('_', s.name)}.log",
                keep_alive=False,  # the server stops when cmcoder closes the connection
            )
        if not cfg.url:
            raise ValueError("an MCP server needs either `command` or `url`")
        url = expand(cfg.url)
        headers = {k: expand(v) for k, v in cfg.headers.items()}
        ssl_ctx = build_ssl_context(TransportOptions(ca_cert_path=cfg.ca_cert_path))

        def http_client(
            headers: dict[str, str] | None = None,
            timeout: httpx2.Timeout | None = None,
            auth: httpx2.Auth | None = None,
            **kwargs: Any,
        ) -> httpx2.AsyncClient:
            # cmcoder's TLS (the OS certificate store, or caCertPath; never
            # unverified) and HTTPS_PROXY / NO_PROXY from the environment.
            return httpx2.AsyncClient(
                headers=headers,
                timeout=timeout or httpx2.Timeout(30.0, read=300.0),
                auth=auth,
                follow_redirects=kwargs.get("follow_redirects", True),
                verify=ssl_ctx,
                trust_env=True,
            )

        if cfg.transport == "sse":
            return SSETransport(url, headers=headers, httpx_client_factory=http_client)
        return StreamableHttpTransport(url, headers=headers, httpx_client_factory=http_client)

    async def close(self) -> None:
        for s in self.servers:
            s._stop.set()
        tasks = [s._task for s in self.servers if s._task is not None]
        if tasks:
            _, still = await asyncio.wait(tasks, timeout=5)
            for t in still:
                t.cancel()
            await asyncio.gather(*still, return_exceptions=True)


# What FastMCP reads from the environment while it's imported, set only for
# that moment: its settings from a `.env` file in the current folder (the
# user's project, which shouldn't configure cmcoder), and its log output (to
# the terminal, under the REPL's and TUI's screens). Not left set, because
# MCP servers started from here inherit the environment.
_FASTMCP_IMPORT_ENV = {"FASTMCP_ENV_FILE": os.devnull, "FASTMCP_LOG_ENABLED": "false"}


def fastmcp_client() -> Any:
    """FastMCP's `Client`, imported the safe way (see above)."""
    saved = {k: os.environ.get(k) for k in _FASTMCP_IMPORT_ENV}
    os.environ.update(_FASTMCP_IMPORT_ENV)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from fastmcp import Client
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return Client


def _describe(e: BaseException) -> str:
    """The first real error inside (possibly nested) exception groups."""
    while isinstance(e, BaseExceptionGroup) and e.exceptions:
        e = e.exceptions[0]
    text = str(e) or type(e).__name__
    return text.splitlines()[0][:300]


def status_lines(manager: McpManager | None) -> list[str]:
    """For `/mcp`: each server's state, with where to look when one failed."""
    if manager is None or not manager.servers:
        return ["No MCP servers. Add one with `cmcoder mcp add` (see `cmcoder mcp --help`)."]
    lines = [s.summary() for s in manager.servers]
    if not manager.started:
        lines.append("Servers start with your first message.")
    if any(s.status == "failed" and s.config.transport == "stdio" for s in manager.servers):
        lines.append(f"Server output (stderr) is in {config_dir() / 'logs'}.")
    for s in manager.connected():
        names = ", ".join(t.name for t in s.tools)
        lines.append(f"  {s.name} tools: {names}" if names else f"  {s.name}: no tools")
    return lines
