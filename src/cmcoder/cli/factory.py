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
from ..core.permissions import PermissionPolicy
from ..core.prompt import build_system_prompt, load_memory_files
from ..providers.auth import ApiKeyAuth, AuthProvider, NoAuth
from ..providers.openai_compat import OpenAICompatProvider, ProviderError
from ..providers.profiles import ModelProfile, resolve_profile
from ..providers.transport import TransportOptions, build_client
from ..tools.base import ToolContext, output_budget_chars
from ..tools.registry import default_tools

MODEL_INFO_TTL = 24 * 3600


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


async def resolve_model_profile(
    settings: Settings, provider: OpenAICompatProvider, model: str
) -> ModelProfile:
    info = await cached_model_info(provider)
    return resolve_profile(model, settings.model_profiles, info.get(model))


async def build_agent(settings: Settings, opts: AgentOptions) -> Agent:
    # Settings `env` applies to the session, including commands the Bash tool runs.
    os.environ.update({k: str(v) for k, v in settings.env.items()})
    cwd = opts.cwd.resolve()
    root = find_project_root(cwd)
    provider_name, model = settings.resolve_model(opts.model)
    provider = build_provider(settings, provider_name)
    profile = await resolve_model_profile(settings, provider, model)

    policy = PermissionPolicy(
        mode=opts.permission_mode or settings.permissions.default_mode,
        allow=[*settings.permissions.allow, *(opts.allowed_tools or [])],
        deny=[*settings.permissions.deny, *(opts.disallowed_tools or [])],
    )
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

    return Agent(
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
    )
