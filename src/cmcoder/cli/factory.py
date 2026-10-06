"""Build providers and agents from settings."""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Callable
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
from ..core.commands import CommandSource
from ..core.compaction import Summarizer
from ..core.critic import SavedCritique
from ..core.hooks import HookRunner
from ..core.permissions import ModeNotAllowed, PermissionPolicy
from ..core.prompt import (
    CODE_SEARCH_NOTE,
    build_subagent_prompt,
    build_system_prompt,
    load_memory_files,
)
from ..core.sessions import SessionLog, cleanup, find_session, list_sessions, load
from ..core.skills import SkillTool, load_skills, skills_prompt
from ..core.subagents import ModelChoice, SubagentRuntime
from ..mcp_client import McpManager
from ..providers.auth import ApiKeyAuth, AuthProvider, NoAuth
from ..providers.openai_compat import OpenAICompatProvider, ProviderError
from ..providers.openwebui import OpenWebUIProvider
from ..providers.profiles import ModelProfile, resolve_profile
from ..providers.transport import TransportOptions, build_client
from ..rag.index import CodeIndex, Progress, open_index
from ..rag.setup import status_lines
from ..rag.stores import StoreError
from ..sandbox import make_sandbox
from ..telemetry import from_settings as telemetry_from_settings
from ..tools.base import ToolContext, output_budget_chars
from ..tools.code_search import CodeSearchTool
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
    cls = OpenWebUIProvider if cfg.type == "openwebui" else OpenAICompatProvider
    return cls(
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


async def _ollama_window(
    settings: Settings, provider: OpenWebUIProvider, model: str, mi: dict[str, Any]
) -> dict[str, Any]:
    """An Ollama model behind Open WebUI: cmcoder's usual window, but never
    more than the model was trained for (cmcoder sends it as num_ctx)."""
    trained = await provider.ollama_context_length(str(mi.get("base_model") or model))
    base = resolve_profile(model, settings.model_profiles, None)
    if trained and trained < base.context_window:
        return {
            **mi,
            "max_input_tokens": trained,
            "window_source": "Ollama model (trained length, via Open WebUI)",
        }
    return mi


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
    critique: bool | None = None  # --critic; None: settings critic.enabled
    append_system_prompt: str | None = None
    ask: AskFn | None = None
    persist_rules: bool = True
    continue_session: bool = False  # --continue: the latest session in this project
    resume: str | None = None  # --resume ID (or a unique prefix of it)
    frontend: str = "cli"  # "cli", "tui", "print" (-p), or the IDE (--client), for telemetry


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
    if mi and mi.get("backend") == "ollama" and isinstance(provider, OpenWebUIProvider):
        mi = await _ollama_window(settings, provider, model, mi)
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
    sandbox, sandbox_warning = make_sandbox(settings.sandbox, root)
    code_index, index_warning = await open_code_index(settings, root, provider)
    telemetry = telemetry_from_settings(settings.telemetry)
    skills = load_skills(root)
    skills_section = skills_prompt(skills)
    system_prompt = build_system_prompt(
        cwd,
        root,
        tier=profile.prompt_tier,
        model=model,
        memory=memory,
        append=opts.append_system_prompt,
        skills=skills_section,
        sandbox=sandbox.prompt_note() if sandbox else None,
        code_search=code_index is not None,
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
        [
            *default_tools(),
            *([SkillTool(skills)] if skills else []),
            *([CodeSearchTool()] if code_index is not None else []),
        ],
        policy,
        ToolContext(
            cwd=cwd,
            project_root=root,
            max_output_chars=output_budget_chars(profile.context_window),
            sandbox=sandbox,
            code_index=code_index,
        ),
        system_prompt,
        max_turns=opts.max_turns or settings.max_turns,
        subagent_max_turns=settings.subagent_max_turns,
        max_parallel_subagents=settings.max_parallel_subagents,
        critique=settings.critic.enabled if opts.critique is None else opts.critique,
        critique_rounds=settings.critic.max_rounds,
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
        commands=CommandSource(root, settings.project_trusted),
        telemetry=telemetry,
        auto_context=settings.rag.auto_context if code_index is not None else None,
        subagents=SubagentRuntime(
            root,
            settings.project_trusted,
            resolve_model=lambda ref: resolve_subagent_model(settings, provider, summarizer, ref),
            system_prompt=lambda d, m: build_subagent_prompt(
                d.prompt, cwd, root, model=m, memory=memory, skills=skills_section
            ),
        ),
    )
    agent.saved_critique = SavedCritique()  # the panel's checkbox, from any window
    if sandbox_warning:
        agent.startup_warnings.append(sandbox_warning)
    if index_warning:
        agent.startup_warnings.append(index_warning)
    if code_index is not None and settings.rag.auto_update:
        code_index.start_background_update()
    if telemetry is not None:
        telemetry.session_started(opts.frontend, agent.session_id)
    if opts.continue_session or opts.resume:
        resume_session(agent, settings, root, opts.resume)
    return agent


async def open_code_index(
    settings: Settings, root: Path, provider: OpenAICompatProvider
) -> tuple[CodeIndex | None, str | None]:
    """The project's code index for this session, if code search is on and the
    project has one; else None and, when worth saying, why."""
    try:
        index = open_index(settings, root, build_provider, shared=provider)
    except (SettingsError, StoreError) as e:
        return None, f"Code search is off for this session: {e}"
    if index is None:
        return None, None
    if await index.available():
        return index, None
    await index.close()
    if settings.rag.enabled is True:
        return None, "Code search is on, but this project has no index yet: run `cmcoder index`."
    return None, None


NOT_SET_UP = (
    "Code search isn't set up: run `cmcoder rag setup` in a terminal "
    '(or "Set up code search" in VS Code).'
)


def session_index(agent: Agent, settings: Settings) -> tuple[CodeIndex | None, str | None]:
    """The session's code index, or one opened now (to build it with /index)."""
    if agent.ctx.code_index is not None:
        return agent.ctx.code_index, None
    try:
        shared = agent.provider if isinstance(agent.provider, OpenAICompatProvider) else None
        index = open_index(settings, agent.ctx.project_root, build_provider, shared=shared)
    except (SettingsError, StoreError) as e:
        return None, f"Code search can't start: {e}"
    return (index, None) if index is not None else (None, NOT_SET_UP)


def attach_code_index(agent: Agent, index: CodeIndex, settings: Settings) -> None:
    """Code search for the rest of a session that started without an index."""
    agent.ctx.code_index = index
    agent.tools.setdefault("CodeSearch", CodeSearchTool())
    agent.auto_context = settings.rag.auto_context
    if CODE_SEARCH_NOTE not in agent.system_prompt:
        agent.system_prompt = f"{agent.system_prompt}\n{CODE_SEARCH_NOTE}"
        if agent.messages and agent.messages[0].role == "system":
            agent.messages[0].content = agent.system_prompt


async def index_command(
    agent: Agent,
    settings: Settings,
    arg: str,
    progress: Callable[[Progress], None] | None = None,
) -> list[str]:
    """`/index [status]` in a session (terminal, TUI, VS Code): lines to show."""
    index, problem = session_index(agent, settings)
    if index is None:
        return [problem or NOT_SET_UP]
    attached = index is agent.ctx.code_index
    try:
        if arg == "status":
            if not attached and not await index.available():
                return ["No index for this project yet: /index builds it."]
            return await status_lines(index)
        if arg not in ("", "update", "rebuild", "clear"):
            return ["Usage: /index (build or update the code index) or /index status"]
        if index.read_only:
            return ["This index is read-only: it's kept up to date elsewhere."]
        if arg in ("rebuild", "clear"):
            await index.clear()
            if arg == "clear":
                return ["Deleted this project's index."]
        result = await index.update(progress)
        lines = [
            f"Indexed {result.indexed} files ({result.chunks} pieces) in {result.seconds:.1f}s; "
            f"{result.unchanged} unchanged, {result.removed} removed."
        ]
        if not attached and await index.available():
            attach_code_index(agent, index, settings)
            attached = True
            lines.append("Code search is on for the rest of this session.")
        return lines
    except (StoreError, ProviderError) as e:
        hint = getattr(e, "hint", None)
        return [f"Indexing failed: {e}" + (f"\n{hint}" if hint else "")]
    finally:
        if not attached:
            await index.close()


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


async def resolve_subagent_model(
    settings: Settings,
    main_provider: OpenAICompatProvider,
    summarizer: Summarizer | None,
    ref: str,
) -> ModelChoice | None:
    """A subagent's `model`: "small" (smallFastModel), "subagent" (subagentModel,
    else smallFastModel), or a model name. None: use the main model."""
    if ref == "subagent":
        ref = settings.subagent_model or "small"
    if ref == "small":
        return summarizer
    name, model = settings.resolve_model(ref)
    if summarizer is not None and name == summarizer.provider.name:
        provider = summarizer.provider
    elif name == main_provider.name:
        provider = main_provider
    else:  # a provider nothing else uses would have to be opened and closed per task
        raise SettingsError(f"provider {name!r} isn't the main or the small model's provider")
    profile = await resolve_model_profile(settings, provider, model)
    return ModelChoice(provider, model, profile)


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
