"""`cmcoder doctor`: check the environment, the network path to the model server, and the models."""

from __future__ import annotations

import asyncio
import os
import platform
import socket
import ssl
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from rich.console import Console
from rich.text import Text

from ..compat import SHELL_HELP, find_program, find_shell
from ..config.settings import (
    Settings,
    config_fingerprint,
    find_project_root,
    ignored_settings_message,
    is_approved,
    managed_settings_path,
)
from ..core.hooks import HookRunner
from ..mcp_client import McpManager
from ..providers.auth import ApiKeyAuth
from ..providers.messages import Message, StreamDone, TextDelta, ToolSpec
from ..providers.openai_compat import OpenAICompatProvider, ProviderError, no_tool_support
from ..providers.profiles import ModelProfile, resolve_profile
from ..providers.text_tools import extract
from ..providers.transport import TransportOptions, build_ssl_context, proxy_bypassed
from .factory import PROBE_SOURCE, build_provider, resolve_model_profile, save_learned_window

OK, WARN, FAIL, INFO = "ok", "warn", "fail", "info"
_STYLE = {OK: ("✓", "green"), WARN: ("⚠", "yellow"), FAIL: ("✗", "red"), INFO: ("•", "cyan")}


@dataclass
class Check:
    status: str
    title: str
    detail: str = ""


class Doctor:
    def __init__(self, settings: Settings, console: Console) -> None:
        self.settings = settings
        self.console = console
        self.failed = False

    def report(self, status: str, title: str, detail: str = "") -> None:
        icon, style = _STYLE[status]
        self.console.print(Text(f"{icon} ", style=style) + Text(title))
        if detail:
            for line in detail.splitlines():
                self.console.print(Text(f"    {line}", style="dim"))
        if status == FAIL:
            self.failed = True

    def section(self, title: str) -> None:
        self.console.print(Text(f"\n{title}", style="bold"))

    # ------------------------------------------------------------------

    def check_managed(self) -> None:
        """Report the organisation's managed settings, and whether this user
        could change them (then they don't protect anything)."""
        s = self.settings
        path = Path(s.managed_path) if s.managed_path else managed_settings_path()
        if not s.managed_path:
            self.report(INFO, "No managed settings", f"(an administrator can create {path})")
            return
        p = s.permissions
        enforced = [
            f"bypassPermissions {'disabled' if p.disable_bypass_permissions_mode else 'allowed'}",
            f"high-risk commands: {p.high_risk_commands}",
            f"{len(p.deny)} deny rule(s)",
        ]
        if p.allow_managed_permission_rules_only:
            enforced.append("only managed allow rules")
        if s.lock_providers:
            enforced.append(f"providers locked to: {', '.join(s.providers)}")
        self.report(OK, f"Managed settings in effect: {path}", "; ".join(enforced))
        writable = [q for q in (path, path.parent) if os.access(q, os.W_OK)]
        if writable:
            self.report(
                WARN,
                "Managed settings can be changed by the current user",
                f"{writable[0]} is writable without administrator rights here (or you are "
                "running as an administrator). Restrict it so only administrators can write it.",
            )

    def check_local(self) -> None:
        self.section("Local environment")
        s = self.settings
        self.report(
            INFO,
            "Settings loaded from",
            "\n".join(s.sources) or "(no settings files; using defaults)",
        )
        self.check_managed()
        cwd = Path.cwd().resolve()
        root = find_project_root(cwd)
        self.report(
            INFO,
            f"Project root: {root}",
            "the current folder"
            if root == cwd
            else "found by a .git or .cmcoder folder above "
            "the current one; reads inside it need no approval, so check it is the project "
            "you mean",
        )
        if warning := ignored_settings_message(s):
            self.report(WARN, "Project settings", warning)
        else:
            self.report(
                INFO,
                "Project settings",
                "trusted (cmcoder trust)" if s.project_trusted else "not trusted; nothing ignored",
            )
        self.report(
            INFO,
            f"Platform: {platform.system()} {platform.release()}, Python {platform.python_version()}",
        )
        shell = find_shell()
        if shell:
            self.report(OK, "bash found (Bash tool)", shell)
        else:
            self.report(FAIL, "bash not found (Bash tool)", SHELL_HELP)
        rg = find_program("rg")
        if rg:
            self.report(OK, "ripgrep found (fast Grep/Glob)", rg)
        else:
            self.report(
                WARN,
                "ripgrep (rg) not found",
                "Grep/Glob use the slower built-in search. "
                "Install ripgrep for large repos (winget install BurntSushi.ripgrep.MSVC, "
                "brew install ripgrep, or apt install ripgrep).",
            )
        git = find_program("git")
        if git:
            self.report(OK, "git found", git)
        else:
            self.report(WARN, "git not found", "Prompts won't include git status.")
        if not s.providers:
            self.report(
                FAIL,
                "No model provider configured",
                "Add `providers` to ~/.cmcoder/settings.json or set CMCODER_BASE_URL.",
            )
        if not s.model:
            self.report(FAIL, "No model configured", "Set `model` in settings or CMCODER_MODEL.")
        else:
            self.report(OK, f"Main model: {s.model}")
        if s.small_fast_model:
            self.report(OK, f"Small/fast model: {s.small_fast_model}")
        else:
            self.report(
                WARN,
                "No smallFastModel set",
                "Quick jobs (titles, summaries) will use the main model.",
            )

    async def check_network(self, provider_name: str) -> bool:
        cfg = self.settings.providers[provider_name]
        url = urlparse(cfg.base_url)
        host = url.hostname or ""
        port = url.port or (443 if url.scheme == "https" else 80)
        self.section(f"Model server: {provider_name} ({cfg.base_url})")

        if url.scheme == "http" and host not in ("localhost", "127.0.0.1", "::1"):
            self.report(
                WARN,
                "Plain http:// to a remote host",
                "Code would be sent unencrypted. Use https://.",
            )
        bypass = proxy_bypassed(cfg.base_url)
        if bypass is None:
            self.report(OK, "No HTTPS proxy configured")
        elif bypass:
            self.report(OK, "HTTPS proxy set, and NO_PROXY covers this host")
        else:
            self.report(
                WARN,
                "HTTPS_PROXY is set but NO_PROXY does not include this host",
                f"Internal servers usually need bypassing: add {host} (or its domain) to NO_PROXY.",
            )

        try:
            infos = await asyncio.to_thread(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM)
            addrs = sorted({str(i[4][0]) for i in infos})
            self.report(OK, f"DNS: {host} resolves", ", ".join(addrs))
        except OSError as e:
            self.report(
                FAIL,
                f"DNS: cannot resolve {host}",
                f"{e}\nThis name only resolves on the company network: connect to the VPN.",
            )
            return False

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), cfg.connect_timeout
            )
            writer.close()
            self.report(OK, f"TCP: connected to {host}:{port}")
        except (OSError, TimeoutError) as e:
            self.report(
                FAIL,
                f"TCP: cannot connect to {host}:{port}",
                f"{e or type(e).__name__}\nCheck VPN, firewall or proxy.",
            )
            return False

        if url.scheme == "https":
            ctx = build_ssl_context(TransportOptions(ca_cert_path=cfg.ca_cert_path))
            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection(host, port, ssl=ctx, server_hostname=host),
                    cfg.connect_timeout,
                )
                cert = writer.get_extra_info("peercert") or {}
                writer.close()
                self.report(OK, "TLS: certificate verified", _cert_summary(cert))
            except ssl.SSLCertVerificationError as e:
                cert = await _fetch_unverified_cert(host, port, cfg.connect_timeout)
                detail = f"{e.verify_message or e}\n{_cert_summary(cert)}" if cert else str(e)
                self.report(
                    FAIL,
                    "TLS: certificate not trusted",
                    detail + "\nInstall the company root CA in the OS trust store, or set "
                    "`caCertPath` in settings (or CMCODER_CA_CERT) to the CA's .pem file.",
                )
                return False
            except (OSError, ssl.SSLError, TimeoutError) as e:
                self.report(FAIL, "TLS: handshake failed", str(e) or type(e).__name__)
                return False
        return True

    async def check_api(self, provider_name: str, models: list[str], probe: bool) -> None:
        provider = build_provider(self.settings, provider_name)
        try:
            cfg = self.settings.providers[provider_name]
            if cfg.auth.type == "apiKey":
                auth = provider.auth
                assert isinstance(auth, ApiKeyAuth)
                if auth.resolve_key():
                    self.report(OK, f"API key found: {auth.describe()}")
                else:
                    self.report(FAIL, "No API key", "Run `cmcoder login` or set CMCODER_API_KEY.")
                    return
            try:
                available = await provider.list_models()
                self.report(
                    OK,
                    f"API: authenticated, {len(available)} models available",
                    ", ".join(available[:20]),
                )
            except ProviderError as e:
                self.report(FAIL, "API: GET /models failed", str(e))
                return
            for m in models:
                if m in available:
                    self.report(OK, f"Model '{m}' is available")
                else:
                    self.report(
                        FAIL,
                        f"Model '{m}' is not in the server's model list",
                        "Use one of the names above (LiteLLM aliases or Open WebUI model ids).",
                    )
            info = await provider.model_info()
            if provider.kind == "openwebui":
                for m in models:
                    backend = info.get(m, {}).get("backend", "?")
                    where = (
                        "Ollama"
                        if backend == "ollama"
                        else "an OpenAI-compatible server"
                        if backend == "openai"
                        else f"a '{backend}' connection"
                    )
                    self.report(INFO, f"{m}: served by {where} through Open WebUI")
            elif info:
                for m in models:
                    mi = info.get(m, {})
                    if mi:
                        self.report(
                            INFO,
                            f"{m}: server reports context {mi.get('max_input_tokens', '?')} tokens, "
                            f"max output {mi.get('max_output_tokens', '?')}, "
                            f"tool calling {mi.get('supports_function_calling', '?')}",
                        )
            else:
                self.report(
                    INFO, "Server does not expose /model/info; using built-in model profiles"
                )
            if probe:
                for m in models:
                    if m in available:
                        await self.probe_model(provider, m, info.get(m))
        finally:
            await provider.aclose()

    async def check_context_window(
        self, provider: OpenAICompatProvider, model: str
    ) -> ModelProfile:
        """Ask the server for the real window (fresh, not from the cache) and
        report which value cmcoder will use and where it came from."""
        profile = await resolve_model_profile(self.settings, provider, model, probe=False)
        if profile.backend == "ollama":
            self.report(
                OK,
                f"{model}: context window {profile.context_window:,} tokens, sent to Ollama "
                "as num_ctx with every request",
                f"from {profile.context_window_source}. Ollama drops the start of longer "
                "prompts silently, so cmcoder sets it itself. A larger value needs more GPU "
                'memory on the server: set "contextWindow" in modelProfiles to change it.',
            )
            return profile
        tokens = await provider.probe_context_window(
            model, resolve_profile(model, self.settings.model_profiles)
        )
        save_learned_window(provider.base_url, model, tokens, PROBE_SOURCE)
        profile = await resolve_model_profile(self.settings, provider, model, probe=False)
        heard = f"server says {tokens:,} tokens" if tokens else "server did not state its limit"
        detail = f"{heard}; using {profile.context_window:,} from {profile.context_window_source}."
        if tokens and profile.context_window > tokens:
            self.report(
                WARN,
                f"{model}: context window set to {profile.context_window:,} tokens, "
                f"but the server allows {tokens:,}",
                f"{detail} Requests will fail until contextWindow in modelProfiles is lowered.",
            )
        elif profile.context_window <= 32_768:
            self.report(
                WARN,
                f"{model}: context window is {profile.context_window:,} tokens",
                f"{detail} Small for an agent: conversations will be summarised often. If GPU "
                "memory allows, ask the admin to serve a longer context (e.g. vLLM "
                "--max-model-len 65536 or more; Qwen3 supports long context via YaRN).",
            )
        else:
            self.report(OK, f"{model}: context window {profile.context_window:,} tokens", detail)
        return profile

    async def probe_model(
        self, provider: OpenAICompatProvider, model: str, info: dict[str, Any] | None
    ) -> None:
        self.console.print(Text(f"  probing {model} …", style="dim"))
        profile = await self.check_context_window(provider, model)
        # 1. streaming + latency
        start = time.monotonic()
        first: float | None = None
        done: StreamDone | None = None
        try:
            async for sev in provider.stream_chat(
                model,
                [Message.user("Reply with exactly the word OK.")],
                [],
                profile,
                max_tokens=512,
            ):
                if first is None and isinstance(sev, TextDelta):
                    first = time.monotonic() - start
                if isinstance(sev, StreamDone):
                    done = sev
        except ProviderError as e:
            self.report(FAIL, f"{model}: chat request failed", str(e))
            return
        total = time.monotonic() - start
        if done is None:
            self.report(FAIL, f"{model}: stream ended without a reply")
            return
        ttft = f"{first:.1f}s" if first is not None else "n/a"
        usage = "reported" if not done.usage.estimated else "not reported (estimated)"
        self.report(
            OK,
            f"{model}: streaming works",
            f"first token {ttft}, total {total:.1f}s, usage {usage}, "
            f"reasoning {'present' if done.message.reasoning else 'none'}",
        )
        if model in provider.open_think_models:
            self.report(
                INFO,
                f"{model}: output has only a closing </think> (Qwen3 Thinking-2507 style)",
                "Handled automatically. A reasoning parser on the backend (vLLM "
                "--reasoning-parser qwen3) would make it cleaner.",
            )

        # 2. thinking switch
        switch = "Ollama's think option" if profile.backend == "ollama" else profile.thinking_switch
        if profile.thinking_switch != "none":
            try:
                quick: StreamDone | None = None
                async for sev in provider.stream_chat(
                    model, [Message.user("Say hi.")], [], profile, thinking=False, max_tokens=256
                ):
                    if isinstance(sev, StreamDone):
                        quick = sev
                if quick and quick.message.reasoning.strip():
                    self.report(
                        WARN,
                        f"{model}: thinking could not be switched off via {switch}",
                        'The gateway may drop extra params. Set modelProfiles thinkingSwitch to "prompt".',
                    )
                else:
                    self.report(OK, f"{model}: thinking can be switched off ({switch})")
            except ProviderError as e:
                self.report(
                    WARN,
                    f"{model}: request with thinking switch rejected",
                    f'{e}\nSet modelProfiles thinkingSwitch to "prompt" or "none".',
                )

        # 3. tool calling
        tool = ToolSpec(
            "get_weather",
            "Get the current weather for a city.",
            {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
        )
        try:
            result: StreamDone | None = None
            async for sev in provider.stream_chat(
                model,
                [Message.user("What is the weather in Paris? Use the get_weather tool.")],
                [tool],
                profile,
                max_tokens=1024,
            ):
                if isinstance(sev, StreamDone):
                    result = sev
        except ProviderError as e:
            self.report(
                WARN if no_tool_support(e) else FAIL,
                f"{model}: request with tools rejected",
                f"{e}\nThe backend has no native tool calling. cmcoder switches to prompted tool "
                'calls automatically; set "toolCalling": "prompted" in modelProfiles to skip the '
                "retry, or enable tool calling on the backend.",
            )
            return
        calls = result.message.tool_calls if result else []
        if calls and calls[0].name == "get_weather" and "paris" in calls[0].arguments.lower():
            self.report(
                OK, f"{model}: native tool calling works", f"{calls[0].name}({calls[0].arguments})"
            )
        elif result and extract(result.message.content, {"get_weather"})[1]:
            self.report(
                WARN,
                f"{model}: tool calls come back as text (<tool_call> tags)",
                "cmcoder parses them automatically. Enabling the backend's tool parser is still "
                "better (vLLM --enable-auto-tool-choice --tool-call-parser hermes).",
            )
        elif result and "get_weather" in result.message.content:
            self.report(
                FAIL,
                f"{model}: model wrote the tool call as text instead of a tool call",
                'Set "toolCalling": "prompted" in modelProfiles, or enable tool calling on the '
                "backend (vLLM --enable-auto-tool-choice --tool-call-parser hermes).",
            )
        else:
            hint = "The backend may not support tool calling for this model."
            if provider.kind == "openwebui":
                hint += (
                    " In Open WebUI, set the model's Function Calling to Native (Admin Panel → "
                    "Settings → Models → the model → Advanced Params); with an Ollama backend "
                    "the model itself must support tools."
                )
            self.report(FAIL, f"{model}: no tool call returned", hint)

    def check_hooks(self) -> None:
        runner = HookRunner(self.settings, find_project_root(Path.cwd().resolve()))
        if not runner.hooks:
            return
        self.section("Hooks")
        for hook in runner.hooks:
            match = f" [{hook.matcher}]" if hook.matcher else ""
            approved = ""
            if hook.origin == "project":
                fp = config_fingerprint(hook.command)
                approved = (
                    " · approved"
                    if is_approved(runner.root, hook.key, fp)
                    else " · asks before it first runs"
                )
            self.report(
                INFO, f"{hook.event}{match}: {hook.command.command}", f"{hook.origin}{approved}"
            )
        if find_shell() is None:
            self.report(FAIL, "Hooks need bash to run", SHELL_HELP)

    def check_sandbox(self) -> None:
        from ..sandbox import detect

        cfg = self.settings.sandbox
        self.section("Bash sandbox")
        if cfg.enabled is False:
            self.report(INFO, "Off in settings (sandbox.enabled: false)")
            return
        avail = detect()
        if not avail.ok:
            status = INFO if sys.platform == "win32" else WARN
            self.report(status, "Not available: Bash commands run unsandboxed", avail.reason)
            return
        how = "bubblewrap" if avail.kind == "bwrap" else "macOS sandbox-exec"
        hosts = ", ".join(cfg.network.allowed_hosts) or "none (add sandbox.network.allowedHosts)"
        self.report(
            OK,
            f"Bash commands run in a sandbox ({how})",
            f"writes: the project and a temp folder{' + ' + ', '.join(cfg.writable_paths) if cfg.writable_paths else ''}\n"
            f"network: {hosts}\n"
            f"prompts: {'none for sandboxed commands' if cfg.auto_allow else 'as usual'}; "
            f"leaving the sandbox: {'asks each time' if cfg.allow_unsandboxed_commands else 'not allowed'}",
        )

    async def check_telemetry(self) -> None:
        from ..telemetry import from_settings

        telemetry = from_settings(self.settings.telemetry)
        if telemetry is None:
            if self.settings.telemetry.enabled:
                self.section("Telemetry")
                self.report(WARN, "Telemetry is on but has no endpoint", "Set telemetry.endpoint.")
            return
        self.section("Telemetry")
        try:
            problem = await telemetry.check()
        finally:
            await telemetry.client.aclose()
        if problem:
            self.report(FAIL, f"OpenTelemetry collector not reachable: {telemetry.url}", problem)
        else:
            self.report(
                OK,
                f"OpenTelemetry metrics go to {telemetry.url}",
                "Counts and timings only: no prompts, code, paths or commands.",
            )

    def check_extensions(self) -> None:
        """Custom commands, subagents and skills: what's found, and from where."""
        from ..core.commands import load_commands
        from ..core.skills import load_skills
        from ..core.subagents import load_agents, untrusted_project_agents

        root = find_project_root(Path.cwd().resolve())
        trusted = self.settings.project_trusted
        commands = load_commands(root)
        agents = {n: a for n, a in load_agents(root, trusted).items() if a.origin != "built-in"}
        skills = load_skills(root)
        ignored = [] if trusted else untrusted_project_agents(root)
        if not (commands or agents or skills or ignored):
            return
        self.section("Commands, agents and skills")
        for c in commands.values():
            tools = (
                f" · allows {', '.join(c.allowed_tools)} for its turn" if c.allowed_tools else ""
            )
            if c.origin == "project" and c.allowed_tools and not trusted:
                tools = " · its allowed-tools apply once the project is trusted"
            self.report(INFO, f"/{c.name}: {c.description or '(no description)'}", c.origin + tools)
        for a in agents.values():
            self.report(
                INFO,
                f"agent {a.name}: {a.description}",
                f"{a.origin} · tools: {', '.join(a.tools) if a.tools else 'all'}"
                f" · model: {a.model or 'inherit'}",
            )
        if ignored:
            self.report(
                WARN,
                f"Project agents not used: {', '.join(ignored)}",
                "the project isn't trusted (`cmcoder trust`)",
            )
        for sk in skills.values():
            self.report(INFO, f"skill {sk.name}: {sk.description}", sk.origin)

    async def check_mcp(self, probe: bool) -> None:
        """MCP servers: configured, approved, and (with probes) whether they start."""
        s = self.settings
        if not s.mcp_servers and not s.project_mcp_servers:
            return
        self.section("MCP servers")
        root = find_project_root(Path.cwd().resolve())
        manager = McpManager(s, root)
        if not probe:
            for server in manager.servers:
                self.report(INFO, f"{server.name}: {server.config.describe()}", server.origin)
            return
        async for warning in manager.start(None):
            del warning  # each failed server is reported below
        for server in manager.servers:
            if server.status == "connected":
                self.report(
                    OK, f"{server.name}: {len(server.tools)} tools", server.config.describe()
                )
            elif server.status == "not approved":
                self.report(
                    WARN, f"{server.name}: not approved", f"cmcoder mcp approve {server.name}"
                )
            elif server.status in ("disabled", "blocked"):
                self.report(INFO, f"{server.name}: {server.status}", server.error)
            else:
                self.report(FAIL, f"{server.name}: could not start", server.error)
        await manager.close()

    async def check_code_search(self, probe: bool) -> None:
        """Phase 5: the embedding model answers, and the project's index."""
        cfg = self.settings.rag
        if cfg.enabled is False:
            return
        self.section("Code search (RAG)")
        if not cfg.embedding_model:
            self.report(INFO, "Not set up", "`cmcoder rag setup` chooses an embedding model.")
            return
        try:
            provider_name, model = self.settings.resolve_model(cfg.embedding_model)
            provider = build_provider(self.settings, provider_name)
        except Exception as e:  # a settings problem, reported as such
            self.report(FAIL, f"Embedding model {cfg.embedding_model}: {e}")
            return
        try:
            if probe:
                from ..rag.embed import Embedder

                embedder = Embedder(provider, model)
                try:
                    await embedder.embed(["def hello(): return 'world'"])
                except ProviderError as e:
                    self.report(FAIL, f"Embedding model {provider_name}:{model}", str(e))
                    return
                self.report(
                    OK,
                    f"Embedding model {provider_name}:{model} answers "
                    f"({embedder.dim} dimensions, {embedder.gateway})",
                )
            else:
                self.report(INFO, f"Embedding model {provider_name}:{model} (not checked)")
        finally:
            await provider.aclose()

    async def run(self, model_refs: list[str], probe: bool) -> int:
        self.check_local()
        by_provider: dict[str, list[str]] = {}
        for ref in model_refs:
            try:
                p, m = self.settings.resolve_model(ref)
            except Exception as e:
                self.report(FAIL, str(e))
                continue
            by_provider.setdefault(p, [])
            if m not in by_provider[p]:
                by_provider[p].append(m)
        for provider_name, models in by_provider.items():
            if await self.check_network(provider_name):
                await self.check_api(provider_name, models, probe)
        await self.check_mcp(probe)
        self.check_hooks()
        self.check_sandbox()
        await self.check_code_search(probe)
        await self.check_telemetry()
        self.check_extensions()
        self.console.print()
        if self.failed:
            self.console.print(
                "[bold red]Some checks failed.[/bold red] Fix the items marked ✗ above."
            )
            return 1
        self.console.print("[bold green]All checks passed.[/bold green]")
        return 0


def _name(parts: Any) -> str:
    try:
        for rdn in parts:
            for key, value in rdn:
                if key == "commonName":
                    return str(value)
        return ", ".join(f"{k}={v}" for rdn in parts for k, v in rdn)
    except (TypeError, ValueError):
        return "?"


def _cert_summary(cert: dict[str, Any]) -> str:
    if not cert:
        return ""
    return (
        f"subject: {_name(cert.get('subject', ()))}\n"
        f"issuer:  {_name(cert.get('issuer', ()))}\n"
        f"expires: {cert.get('notAfter', '?')}"
    )


async def _fetch_unverified_cert(host: str, port: int, timeout: float) -> dict[str, Any] | None:
    """Fetch the server certificate *without* verifying it, only to show who issued it.

    No application data is sent on this connection.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port, ssl=ctx, server_hostname=host), timeout
        )
        der = writer.get_extra_info("ssl_object").getpeercert(binary_form=True)
        writer.close()
    except (OSError, ssl.SSLError, TimeoutError, AttributeError):
        return None
    if not der:
        return None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as f:
            f.write(ssl.DER_cert_to_PEM_cert(der))
            name = f.name
        try:
            return dict(ssl._ssl._test_decode_cert(name))  # type: ignore[attr-defined]
        finally:
            os.unlink(name)
    except Exception:
        return None
