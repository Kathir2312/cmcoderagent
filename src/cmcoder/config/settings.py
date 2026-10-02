"""Layered settings, mirroring Claude Code.

Later layers override earlier ones:
  1. user        ~/.cmcoder/settings.json
  2. project     <project>/.cmcoder/settings.json        (committed)
  3. local       <project>/.cmcoder/settings.local.json  (git-ignored)
  4. env vars    CMCODER_* (and OPENAI_* fallbacks)
  5. CLI flags   (applied by the CLI)
and on top of all of them, applied last and impossible to override:
  0. managed     the admin-only managed-settings.json (see managed_settings_path)
"""

from __future__ import annotations

import json
import os
import sys
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
    # "ask": high-risk shell commands always need approval; "deny": never run.
    high_risk_commands: Literal["ask", "deny"] = Field("ask", alias="highRiskCommands")
    # Managed settings only (ignored in other files):
    disable_bypass_permissions_mode: Literal["disable"] | None = Field(
        None, alias="disableBypassPermissionsMode"
    )
    allow_managed_permission_rules_only: bool = Field(
        False, alias="allowManagedPermissionRulesOnly"
    )


class Settings(_Model):
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    model: str | None = None
    small_fast_model: str | None = Field(None, alias="smallFastModel")
    model_profiles: list[dict[str, Any]] = Field(default_factory=list, alias="modelProfiles")
    permissions: PermissionsConfig = Field(
        default_factory=lambda: PermissionsConfig.model_validate({})
    )
    max_turns: int = Field(50, alias="maxTurns")
    # Summarise older turns when the prompt reaches this share of the window.
    auto_compact: bool = Field(True, alias="autoCompact")
    auto_compact_threshold: float = Field(0.8, alias="autoCompactThreshold", ge=0.3, le=0.95)
    env: dict[str, str] = Field(default_factory=dict)
    # Managed settings only: use only the providers the managed file defines.
    lock_providers: bool = Field(False, alias="lockProviders")
    # Where each layer came from, for `cmcoder doctor`.
    sources: list[str] = Field(default_factory=list, exclude=True)
    # The managed settings file in force, if any.
    managed_path: str | None = Field(None, exclude=True)

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


def managed_settings_path() -> Path:
    """Where the organisation's managed settings live: a location only
    administrators can write. Deliberately not configurable by environment
    variable or flag, so a user can't point cmcoder somewhere else."""
    if sys.platform == "win32":
        from ..compat import program_files_dir

        # Not C:\\ProgramData: ordinary users can create folders there.
        return program_files_dir() / "cmcoder" / "managed-settings.json"
    if sys.platform == "darwin":
        return Path("/Library/Application Support/cmcoder/managed-settings.json")
    return Path("/etc/cmcoder/managed-settings.json")


# Keys that only count in the managed file: in any other file they would let
# a project (or a user) speak for the organisation.
_MANAGED_ONLY_PERMISSIONS = ("disableBypassPermissionsMode", "allowManagedPermissionRulesOnly")
_MANAGED_ONLY_TOP = ("lockProviders",)


def _strip_managed_only(layer: dict[str, Any]) -> dict[str, Any]:
    layer = {k: v for k, v in layer.items() if k not in _MANAGED_ONLY_TOP}
    perms = layer.get("permissions")
    if isinstance(perms, dict):
        layer["permissions"] = {
            k: v for k, v in perms.items() if k not in _MANAGED_ONLY_PERMISSIONS
        }
    return layer


def read_managed_settings(path: Path | None = None) -> dict[str, Any]:
    """The managed settings, or {} if there is no file. Fails closed: a
    file that exists but can't be read or parsed stops cmcoder."""
    path = path or managed_settings_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as e:
        raise SettingsError(
            f"Managed settings {path} exist but can't be read ({e}). cmcoder won't start "
            "without them; ask your administrator."
        ) from e
    try:
        data = json.loads(text)
    except ValueError as e:
        raise SettingsError(
            f"Managed settings {path} are not valid JSON ({e}). cmcoder won't start until "
            "your administrator fixes them."
        ) from e
    if not isinstance(data, dict):
        raise SettingsError(f"Managed settings {path} must contain a JSON object.")
    return data


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
        elif k == "highRiskCommands" and out.get(k) == "deny":
            # The strictest layer wins: a project cannot relax a user's "deny".
            continue
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


def env_api_key_source(environ: dict[str, str] | None = None) -> tuple[str | None, str | None]:
    """(key, variable name) of an API key set in the environment.

    OPENAI_API_KEY is only used when the endpoint also comes from
    OPENAI_BASE_URL, so an unrelated OpenAI key is never sent to the
    company gateway.
    """
    env = os.environ if environ is None else environ
    if env.get("CMCODER_API_KEY"):
        return env["CMCODER_API_KEY"], "CMCODER_API_KEY"
    if env.get("OPENAI_API_KEY") and env.get("OPENAI_BASE_URL") and not env.get("CMCODER_BASE_URL"):
        return env["OPENAI_API_KEY"], "OPENAI_API_KEY"
    return None, None


def env_api_key(environ: dict[str, str] | None = None) -> str | None:
    return env_api_key_source(environ)[0]


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
        layer = _strip_managed_only(_read_json(path))
        if layer:
            merged = deep_merge(merged, layer)
            sources.append(str(path))
    managed_file = managed_settings_path()
    managed = read_managed_settings(managed_file)
    env = env_layer(environ)
    if managed.get("lockProviders"):
        env.pop("providers", None)  # no gateway URL from the environment
    if env:
        # An env base URL on its own (no settings providers) defines "default";
        # with providers configured it overrides only "default" if present.
        merged = deep_merge(merged, env)
        sources.append("environment")
    if managed:
        merged = deep_merge(merged, managed)
        sources.append(f"{managed_file} (managed)")
        perms = merged.setdefault("permissions", {})
        managed_perms = managed.get("permissions") or {}
        if managed_perms.get("allowManagedPermissionRulesOnly"):
            perms["allow"] = list(managed_perms.get("allow") or [])
        if managed.get("lockProviders"):
            if not managed.get("providers"):
                raise SettingsError(
                    f"Managed settings {managed_file} set lockProviders but define no providers."
                )
            merged["providers"] = json.loads(json.dumps(managed["providers"]))
    raw_headers = (environ if environ is not None else os.environ).get("CMCODER_CUSTOM_HEADERS")
    if raw_headers:
        extra = _parse_headers(raw_headers)
        for prov in (merged.get("providers") or {}).values():
            if isinstance(prov, dict):
                prov["headers"] = {**(prov.get("headers") or {}), **extra}
    try:
        settings = Settings.model_validate(merged)
    except ValueError as e:
        hint = ""
        if any(f"providers.{k}" in str(e) for k in ("model", "smallFastModel", "permissions")):
            hint = (
                "\nhint: `model` and `smallFastModel` go at the top level of settings.json, "
                'next to "providers", not inside it (see docs/settings.example.json).'
            )
        raise SettingsError(f"Invalid settings: {e}{hint}") from e
    settings.sources = sources
    if managed:
        settings.managed_path = str(managed_file)
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
