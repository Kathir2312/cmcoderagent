"""`cmcoder doctor`: check the environment, the network path to the model server, and the models."""

from __future__ import annotations

import asyncio
import os
import platform
import shutil
import socket
import ssl
import tempfile
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from rich.console import Console
from rich.text import Text

from ..compat import SHELL_HELP, find_shell
from ..config.settings import Settings
from ..providers.auth import ApiKeyAuth
from ..providers.messages import Message, StreamDone, TextDelta, ToolSpec
from ..providers.openai_compat import OpenAICompatProvider, ProviderError
from ..providers.profiles import ModelProfile, resolve_profile
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

    def check_local(self) -> None:
        self.section("Local environment")
        s = self.settings
        self.report(
            INFO,
            "Settings loaded from",
            "\n".join(s.sources) or "(no settings files; using defaults)",
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
        rg = shutil.which("rg")
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
        git = shutil.which("git")
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
                        "Use one of the names above (LiteLLM aliases).",
                    )
            info = await provider.model_info()
            if info:
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
                        f"{model}: thinking could not be switched off via {profile.thinking_switch}",
                        'The gateway may drop extra params. Set modelProfiles thinkingSwitch to "prompt".',
                    )
                else:
                    self.report(
                        OK, f"{model}: thinking can be switched off ({profile.thinking_switch})"
                    )
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
                FAIL,
                f"{model}: request with tools rejected",
                f"{e}\nThe backend may not have tool calling enabled.",
            )
            return
        calls = result.message.tool_calls if result else []
        if calls and calls[0].name == "get_weather" and "paris" in calls[0].arguments.lower():
            self.report(
                OK, f"{model}: native tool calling works", f"{calls[0].name}({calls[0].arguments})"
            )
        elif result and "get_weather" in result.message.content:
            self.report(
                FAIL,
                f"{model}: model wrote the tool call as text instead of a tool call",
                "Enable tool calling on the backend behind LiteLLM (e.g. vLLM --enable-auto-tool-choice "
                "--tool-call-parser hermes). Prompted tool calling arrives in Phase 1.",
            )
        else:
            self.report(
                FAIL,
                f"{model}: no tool call returned",
                "The backend may not support tool calling for this model.",
            )

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
