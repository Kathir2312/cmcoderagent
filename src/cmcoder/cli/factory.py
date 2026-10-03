"""Build providers and agents from settings."""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config.settings import (
    ProviderConfig,
    Settings,
    SettingsError,
    add_local_allow_rule,
    config_dir,
    env_api_key_source,
    find_project_root,
)
from ..core.agent import Agent, AskFn
from ..core.compaction import Summarizer
from ..core.hooks import HookRunner
from ..core.permissions import ModeNotAllowed, PermissionPolicy
from ..core.prompt import build_system_prompt, load_memory_files
from ..core.sessions import SessionLog, cleanup, find_session, list_sessions, load
from ..mcp_client import McpManager
from ..providers.auth import ApiKeyAuth, AuthProvider, NoAuth
from ..providers.openai_compat import OpenAICompatProvider, ProviderError
from ..providers.profiles import ModelProfile, resolve_profile
from ..providers.transport import TransportOptions, build_client
from ..tools.base import ToolContext, output_budget_chars
from ..tools.registry import default_tools

MODEL_INFO_TTL = 24 * 3600
LEARNED_WINDOW_TTL = 30 * 24 * 3600
UNKNOWN_WINDOW_TTL = 24 * 3600  # re-probe daily when the server didn't say
PROBE_SOURCE = "server limit (probe)"
ERROR_SOURCE = "server limit (from its error)"


def build_auth(name: str, cfg: ProviderConfig) -> AuthProvider:
    if cfg.auth.type == "none":
        return NoAuth()
    key, source = env_api_key_source()
    return ApiKeyAuth(name, explicit_key=key, explicit_source=source or "environment")


def build_provider(settings: Settings, name: str) -> OpenAICompatProvider:
    cfg = settings.providers.get(name)
    if cfg is None:
        raise SettingsError(
            f"Unknown provider {name!r}. Configured: {', '.join(settings.providers) or 'none'}"
        )
    opts = TransportOptions(
        ca_cert_path=cfg.ca_cert_path,
        connect_timeout=cfg.connect_timeout,
        read_timeout=cfg.read_timeout,
    )
    return OpenAICompatProvider(
        name,
        cfg.base_url,
        build_auth(name, cfg),
        build_client(opts, headers=cfg.headers),
        max_retries=cfg.max_retries,
    )


def _cache_path(provider: str) -> Path:
    return config_dir() / "cache" / f"model_info_{provider}.json"


async def cached_model_info(
    provider: OpenAICompatProvider, timeout: float = 3.0
) -> dict[str, dict[str, Any]]:
    """LiteLLM model info (context window etc.), cached for a day. Best effort."""
    path = _cache_path(provider.name)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if (
            time.time() - data.get("fetched_at", 0) < MODEL_INFO_TTL
            and data.get("base_url") == provider.base_url
        ):
            return dict(data.get("models", {}))
    except (OSError, ValueError):
        pass
    try:
        info = await asyncio.wait_for(provider.model_info(), timeout)
    except (ProviderError, TimeoutError):
        return {}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"fetched_at": time.time(), "base_url": provider.base_url, "models": info}),
            encoding="utf-8",
        )
    except OSError:
        pass
    return info


def _learned_path() -> Path:
    return config_dir() / "cache" / "context_windows.json"


def _read_learned() -> dict[str, Any]:
    try:
        data = json.loads(_learned_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load_learned_window(base_url: str, model: str) -> tuple[int | None, str] | None:
    """A context window learned from the server: (tokens, source), with
    tokens None if a recent probe got no answer. None if nothing is cached."""
    entry = _read_learned().get(base_url, {}).get(model)
    if not isinstance(entry, dict):
        return None
    tokens = entry.get("tokens")
    ttl = LEARNED_WINDOW_TTL if tokens else UNKNOWN_WINDOW_TTL
    if time.time() - float(entry.get("at", 0)) > ttl:
        return None
    return (int(tokens) if tokens else None), str(entry.get("source", PROBE_SOURCE))


def save_learned_window(base_url: str, model: str, tokens: int | None, source: str) -> None:
    data = _read_learned()
    data.setdefault(base_url, {})[model] = {"tokens": tokens, "source": source, "at": time.time()}
    try:
        path = _learned_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


@dataclass
class AgentOptions:
    cwd: Path
    model: str | None = None
    permission_mode: str | None = None
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] | None = None
    max_turns: int | None = None
    append_system_prompt: str | None = None
    ask: AskFn | None = None
    persist_rules: bool = True
    continue_session: bool = False  # --continue: the latest session in this project
    resume: str | None = None  # --resume ID (or a unique prefix of it)


async def resolve_model_profile(
    settings: Settings, provider: OpenAICompatProvider, model: str, probe: bool = True
) -> ModelProfile:
    """The model's profile, with the best-known context window: settings,
    else what the server enforces (learned from an error or a probe, cached),
    else LiteLLM /model/info, else the built-in default.

    When none of these is known, the server is probed once (cached for 30
    days, or a day if it didn't say)."""
    info = await cached_model_info(provider)
    mi = info.get(model)
    entry = load_learned_window(provider.base_url, model)
    if entry is None and probe:
        base = resolve_profile(model, settings.model_profiles, mi)
        if base.context_window_source == "built-in default":
            tokens = await provider.probe_context_window(model, base)
            save_learned_window(provider.base_url, model, tokens, PROBE_SOURCE)
            entry = (tokens, PROBE_SOURCE)
    learned = (entry[0], entry[1]) if entry and entry[0] else None
    return resolve_profile(model, settings.model_profiles, mi, learned)


async def build_agent(settings: Settings, opts: AgentOptions) -> Agent:
    # Settings `env` applies to the session, including commands the Bash tool runs.
    os.environ.update({k: str(v) for k, v in settings.env.items()})
    cwd = opts.cwd.resolve()
    root = find_project_root(cwd)
    provider_name, model = settings.resolve_model(opts.model)

    # The permission policy first: a mode the organisation forbids fails
    # before anything is sent to the gateway.
    perms = settings.permissions
    locked = perms.allow_managed_permission_rules_only
    try:
        policy = PermissionPolicy(
            mode=opts.permission_mode or perms.default_mode,
            # With managed-only rules, --allowedTools is ignored like other allow rules.
            allow=[*perms.allow, *([] if locked else (opts.allowed_tools or []))],
            deny=[*perms.deny, *(opts.disallowed_tools or [])],
            high_risk=perms.high_risk_commands,
            bypass_disabled=perms.disable_bypass_permissions_mode == "disable",
            allow_rules_locked=locked,
        )
    except ModeNotAllowed as e:
        where = "--permission-mode" if opts.permission_mode else "defaultMode in your settings"
        raise SettingsError(f"{e} Remove {where} or choose another mode.") from e

    provider = build_provider(settings, provider_name)
    profile = await resolve_model_profile(settings, provider, model)
    memory = load_memory_files(cwd, root)
    system_prompt = build_system_prompt(
        cwd,
        root,
        tier=profile.prompt_tier,
        model=model,
        memory=memory,
        append=opts.append_system_prompt,
    )

    def save_rule(rule: str) -> None:
        if opts.persist_rules:
            add_local_allow_rule(root, rule)

    summarizer = await build_summarizer(settings, provider)
    session = SessionLog(root) if settings.persist_sessions else None
    if session is not None:
        try:
            cleanup(settings.cleanup_period_days)
        except OSError:
            pass

    agent = Agent(
        provider,
        model,
        profile,
        default_tools(),
        policy,
        ToolContext(
            cwd=cwd,
            project_root=root,
            max_output_chars=output_budget_chars(profile.context_window),
        ),
        system_prompt,
        max_turns=opts.max_turns or settings.max_turns,
        ask=opts.ask,
        on_rule_saved=save_rule,
        summarizer=summarizer,
        on_context_window=lambda m, n: save_learned_window(provider.base_url, m, n, ERROR_SOURCE),
        auto_compact=settings.auto_compact,
        compact_threshold=settings.auto_compact_threshold,
        session=session,
        mcp=McpManager(settings, root)
        if settings.mcp_servers or settings.project_mcp_servers
        else None,
        hooks=runner if (runner := HookRunner(settings, root)).hooks else None,
    )
    if opts.continue_session or opts.resume:
        resume_session(agent, settings, root, opts.resume)
    return agent


def resume_session(agent: Agent, settings: Settings, root: Path, ref: str | None) -> None:
    """Load a saved session into the agent: the latest one, or `ref`."""
    if not settings.persist_sessions:
        raise SettingsError("Sessions are not saved (persistSessions is false).")
    if ref:
        info = find_session(root, ref)
        if info is None:
            raise SettingsError(
                f"No single session in this project matches {ref!r}. "
                "Start cmcoder and use /resume to pick one."
            )
    else:
        sessions = list_sessions(root)
        if not sessions:
            raise SettingsError(
                "No saved conversation with messages in this project to continue. "
                "(A conversation that was cleared or rewound to its first message is empty.)"
            )
        info = sessions[0]
    messages, _meta = load(info.path)
    agent.resume(messages, SessionLog(root, info.session_id))


async def build_summarizer(
    settings: Settings, main_provider: OpenAICompatProvider
) -> Summarizer | None:
    """The small/fast model, used to write compaction summaries."""
    if not settings.small_fast_model:
        return None
    try:
        name, model = settings.resolve_model(settings.small_fast_model)
    except SettingsError:
        return None  # summaries fall back to the main model
    provider = main_provider if name == main_provider.name else build_provider(settings, name)
    profile = await resolve_model_profile(settings, provider, model)
    return Summarizer(provider, model, profile)
