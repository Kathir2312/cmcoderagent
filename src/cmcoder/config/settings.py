"""Layered settings, mirroring Claude Code.

Later layers override earlier ones:
  1. user        ~/.cmcoder/settings.json
  2. project     <project>/.cmcoder/settings.json        (committed)
  3. local       <project>/.cmcoder/settings.local.json  (git-ignored)
  4. env vars    CMCODER_* (and OPENAI_* fallbacks)
  5. CLI flags   (applied by the CLI)
Enterprise policy (always wins) is a Phase 4 item.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PermissionModeName = Literal["default", "acceptEdits", "plan", "bypassPermissions"]


class _Model(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class AuthConfig(_Model):
    type: Literal["apiKey", "none"] = "apiKey"


class ProviderConfig(_Model):
    base_url: str = Field(alias="baseUrl")
    auth: AuthConfig = Field(default_factory=lambda: AuthConfig())
    ca_cert_path: str | None = Field(None, alias="caCertPath")
    headers: dict[str, str] = Field(default_factory=dict)
    connect_timeout: float = Field(15.0, alias="connectTimeout")
    read_timeout: float = Field(300.0, alias="readTimeout")
    max_retries: int = Field(2, alias="maxRetries")


class PermissionsConfig(_Model):
    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)
    default_mode: PermissionModeName = Field("default", alias="defaultMode")


class Settings(_Model):
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    model: str | None = None
    small_fast_model: str | None = Field(None, alias="smallFastModel")
    model_profiles: list[dict[str, Any]] = Field(default_factory=list, alias="modelProfiles")
    permissions: PermissionsConfig = Field(
        default_factory=lambda: PermissionsConfig.model_validate({})
    )
    max_turns: int = Field(50, alias="maxTurns")
    env: dict[str, str] = Field(default_factory=dict)
    # Where each layer came from, for `cmcoder doctor`.
    sources: list[str] = Field(default_factory=list, exclude=True)

    def resolve_model(self, ref: str | None = None) -> tuple[str, str]:
        """Split "provider:model" into (provider, model).

        Model names can themselves contain ":" (e.g. "qwen3:8b"), so the prefix
        only counts as a provider when it names a configured provider.
        """
        ref = ref or self.model
        if not ref:
            raise SettingsError(
                "No model configured. Set `model` in ~/.cmcoder/settings.json or CMCODER_MODEL."
            )
        prefix, sep, rest = ref.partition(":")
        if sep and prefix in self.providers:
            return prefix, rest
        if not self.providers:
            raise SettingsError(
                "No model provider configured. Set `providers` in ~/.cmcoder/settings.json "
                "or CMCODER_BASE_URL."
            )
        return self.default_provider(), ref

    def default_provider(self) -> str:
        return "default" if "default" in self.providers else next(iter(self.providers))


class SettingsError(Exception):
    pass


def config_dir() -> Path:
    return Path(os.environ.get("CMCODER_CONFIG_DIR", Path.home() / ".cmcoder"))


def find_project_root(cwd: Path) -> Path:
    """Nearest ancestor with a .git or .cmcoder folder; else cwd."""
    for d in [cwd, *cwd.parents]:
        if (d / ".git").exists() or (d / ".cmcoder").is_dir():
            return d
    return cwd


def deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        elif k in ("allow", "deny") and isinstance(v, list) and isinstance(out.get(k), list):
            # Permission rules accumulate across layers.
            out[k] = [*out[k], *[x for x in v if x not in out[k]]]
        else:
            out[k] = v
    return out


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except ValueError as e:
        raise SettingsError(f"Invalid JSON in {path}: {e}") from e
    if not isinstance(data, dict):
        raise SettingsError(f"{path} must contain a JSON object")
    return data


def _parse_headers(raw: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in raw.replace(";", "\n").splitlines():
        name, sep, value = line.partition(":")
        if sep and name.strip():
            headers[name.strip()] = value.strip()
    return headers


def env_layer(environ: dict[str, str] | None = None) -> dict[str, Any]:
    env = os.environ if environ is None else environ
    layer: dict[str, Any] = {}
    base_url = env.get("CMCODER_BASE_URL") or env.get("OPENAI_BASE_URL")
    provider: dict[str, Any] = {}
    if base_url:
        provider["baseUrl"] = base_url
    if provider:
        layer["providers"] = {"default": provider}
    if env.get("CMCODER_MODEL"):
        layer["model"] = env["CMCODER_MODEL"]
    if env.get("CMCODER_SMALL_FAST_MODEL"):
        layer["smallFastModel"] = env["CMCODER_SMALL_FAST_MODEL"]
    return layer


def env_api_key(environ: dict[str, str] | None = None) -> str | None:
    env = os.environ if environ is None else environ
    return env.get("CMCODER_API_KEY") or env.get("OPENAI_API_KEY") or None


def load_settings(cwd: Path | None = None, environ: dict[str, str] | None = None) -> Settings:
    cwd = (cwd or Path.cwd()).resolve()
    root = find_project_root(cwd)
    merged: dict[str, Any] = {}
    sources: list[str] = []
    for path in (
        config_dir() / "settings.json",
        root / ".cmcoder" / "settings.json",
        root / ".cmcoder" / "settings.local.json",
    ):
        layer = _read_json(path)
        if layer:
            merged = deep_merge(merged, layer)
            sources.append(str(path))
    env = env_layer(environ)
    if env:
        # An env base URL on its own (no settings providers) defines "default";
        # with providers configured it overrides only "default" if present.
        merged = deep_merge(merged, env)
        sources.append("environment")
    raw_headers = (environ if environ is not None else os.environ).get("CMCODER_CUSTOM_HEADERS")
    if raw_headers:
        extra = _parse_headers(raw_headers)
        for prov in (merged.get("providers") or {}).values():
            if isinstance(prov, dict):
                prov["headers"] = {**(prov.get("headers") or {}), **extra}
    try:
        settings = Settings.model_validate(merged)
    except ValueError as e:
        raise SettingsError(f"Invalid settings: {e}") from e
    settings.sources = sources
    return settings


def add_local_allow_rule(project_root: Path, rule: str) -> Path:
    """Persist an "allow always" answer to .cmcoder/settings.local.json."""
    path = project_root / ".cmcoder" / "settings.local.json"
    data = _read_json(path)
    perms = data.setdefault("permissions", {})
    allow = perms.setdefault("allow", [])
    if rule not in allow:
        allow.append(rule)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path
