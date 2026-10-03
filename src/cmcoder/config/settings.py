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

import hashlib
import json
import os
import sys
from collections.abc import Callable
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


class McpServerConfig(_Model):
    """An MCP server, in Claude Code's `.mcp.json` format.

    Local: `command` (+ `args`, `env`, `cwd`). Remote: `url` (+ `headers`),
    `type` "http" (Streamable HTTP, the default for a URL) or "sse". Values
    may use `${VAR}` / `${VAR:-default}` from the environment, so secrets stay
    out of files."""

    type: Literal["stdio", "http", "sse"] | None = None
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    ca_cert_path: str | None = Field(None, alias="caCertPath")
    # Seconds to connect and list tools, and per tool call.
    startup_timeout: float = Field(30.0, alias="startupTimeout")
    tool_timeout: float = Field(120.0, alias="toolTimeout")
    disabled: bool = False

    @property
    def transport(self) -> Literal["stdio", "http", "sse"]:
        if self.type:
            return self.type
        return "stdio" if self.command else "http"

    def describe(self) -> str:
        """For prompts and listings: what will run, or where it connects."""
        if self.transport == "stdio":
            return " ".join([self.command or "?", *self.args])
        return f"{self.url} ({self.transport})"


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
    # "classic": the prompt_toolkit REPL; "textual": the full-screen UI (also --tui).
    ui: Literal["classic", "textual"] = "classic"
    # Save conversations for --continue / --resume, and delete them after N days.
    persist_sessions: bool = Field(True, alias="persistSessions")
    cleanup_period_days: int = Field(30, alias="cleanupPeriodDays", ge=1)
    # Managed settings only: use only the providers the managed file defines.
    lock_providers: bool = Field(False, alias="lockProviders")
    # MCP servers from your user (and managed) settings.
    mcp_servers: dict[str, McpServerConfig] = Field(default_factory=dict, alias="mcpServers")
    # Managed settings only: server names allowed (if set) and denied.
    allowed_mcp_servers: list[str] | None = Field(None, alias="allowedMcpServers")
    denied_mcp_servers: list[str] = Field(default_factory=list, alias="deniedMcpServers")
    # Where each layer came from, for `cmcoder doctor`.
    sources: list[str] = Field(default_factory=list, exclude=True)
    # Project trust (see load_settings): whether this project's own settings
    # files are trusted, and what was left out of them because they aren't.
    project_trusted: bool = Field(False, exclude=True)
    ignored_project_settings: list[str] = Field(default_factory=list, exclude=True)
    # A trusted project's MCP servers (.mcp.json, .cmcoder/settings*.json);
    # each one still needs approval before it first starts.
    project_mcp_servers: dict[str, McpServerConfig] = Field(default_factory=dict, exclude=True)
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
_MANAGED_ONLY_TOP = ("lockProviders", "allowedMcpServers", "deniedMcpServers")


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
    """Nearest ancestor with a .git or .cmcoder folder; else cwd.

    The project root sets what counts as "inside the project" (reads there
    need no approval), where "always allow" rules are saved, and how
    conversations are grouped, so it must never grow too wide:
    - a drive or filesystem root (E:\\, /) and the home folder never count,
      so a stray E:\\.cmcoder can't make the whole drive one project;
    - the user's own config folder (~/.cmcoder) is not a project marker.
    """
    config = config_dir().resolve()
    stop = {Path.home().resolve()}
    for d in [cwd, *cwd.parents]:
        if d.parent == d or d.resolve() in stop:
            break
        if (d / ".git").exists():
            return d
        marker = d / ".cmcoder"
        if marker.is_dir() and marker.resolve() != config:
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


# --- Project trust ---------------------------------------------------------
#
# A project's .cmcoder/settings.json (and settings.local.json, which a
# repository can commit too) is written by whoever made the repository. Some
# settings would let a cloned repository take over: `env` (e.g. BASH_ENV runs
# a script before every shell command, even an auto-approved `ls`), allow
# rules and permissive modes (commands run without asking), and `providers`
# (a gateway URL of the repository's choosing receives your API key).
#
# - `providers` is never read from a project: gateways belong in your user
#   settings (or the managed settings).
# - `env`, allow rules and the acceptEdits/bypassPermissions modes are used
#   only when the project is trusted: `cmcoder trust`, `--trust-project`
#   (the VS Code extension passes it for workspaces VS Code trusts), or
#   rules cmcoder wrote itself ("Always" answers) in settings.local.json.

_PROJECT_NEVER = ("providers",)
_PERMISSIVE_MODES = ("acceptEdits", "bypassPermissions")


def trust_file() -> Path:
    return config_dir() / "trusted-projects.json"


def _trust_key(root: Path) -> str:
    return os.path.normcase(str(root.resolve()))


def _read_trust() -> dict[str, Any]:
    try:
        data = json.loads(trust_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    projects = data.get("projects") if isinstance(data, dict) else None
    return projects if isinstance(projects, dict) else {}


def _write_trust(projects: dict[str, Any]) -> None:
    path = trust_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"projects": projects}, indent=2) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _entry(root: Path) -> dict[str, Any]:
    return _as_dict(_read_trust().get(_trust_key(root)))


def is_project_trusted(root: Path) -> bool:
    return _entry(root).get("trusted") is True


def set_project_trust(root: Path, trusted: bool) -> None:
    """`cmcoder trust` (or `--revoke`)."""
    projects = _read_trust()
    key = _trust_key(root)
    entry = _as_dict(projects.get(key))
    if trusted:
        projects[key] = {**entry, "trusted": True}
    else:
        projects.pop(key, None)
    _write_trust(projects)


def config_fingerprint(config: BaseModel) -> str:
    """Identifies an MCP server or hook's exact configuration: approving one
    covers that configuration only, so a changed command asks again."""
    data = json.dumps(config.model_dump(by_alias=True, exclude_none=True), sort_keys=True)
    return hashlib.sha256(data.encode()).hexdigest()


def is_approved(root: Path, key: str, fingerprint: str) -> bool:
    """Whether the user approved this project's `key` (e.g. "mcp:github") as configured."""
    return _as_dict(_entry(root).get("approved")).get(key) == fingerprint


def approve(root: Path, key: str, fingerprint: str) -> None:
    projects = _read_trust()
    k = _trust_key(root)
    entry = _as_dict(projects.get(k))
    approved = {**_as_dict(entry.get("approved")), key: fingerprint}
    projects[k] = {**entry, "approved": approved}
    _write_trust(projects)


def _fingerprint(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _local_is_own(root: Path, path: Path) -> bool:
    """settings.local.json exactly as cmcoder last wrote it ("Always" answers)."""
    recorded = _entry(root).get("local_sha256")
    return recorded is not None and recorded == _fingerprint(path)


def _filter_project_layer(layer: dict[str, Any], trusted: bool) -> tuple[dict[str, Any], list[str]]:
    """A project settings layer without what it may not set: (layer, left out)."""
    out = dict(layer)
    dropped = [k for k in _PROJECT_NEVER if out.pop(k, None) is not None]
    if trusted:
        return out, dropped
    if out.pop("env", None):
        dropped.append("env")
    if servers := out.pop("mcpServers", None):
        names = ", ".join(servers) if isinstance(servers, dict) else "?"
        dropped.append(f"mcpServers ({names})")
    perms = out.get("permissions")
    if isinstance(perms, dict):
        perms = dict(perms)
        if perms.get("allow"):
            dropped.append("permissions.allow (" + ", ".join(map(str, perms.pop("allow"))) + ")")
        if perms.get("defaultMode") in _PERMISSIVE_MODES:
            dropped.append(f"permissions.defaultMode ({perms.pop('defaultMode')})")
        out["permissions"] = perms
    return out, dropped


def project_settings_preview(root: Path) -> list[str]:
    """What trusting this project would enable (for `cmcoder trust`)."""
    out: list[str] = []
    for path in (root / ".cmcoder" / "settings.json", root / ".cmcoder" / "settings.local.json"):
        try:
            layer = _read_json(path)
        except SettingsError as e:
            out.append(str(e))
            continue
        _, dropped = _filter_project_layer(_strip_managed_only(layer), trusted=False)
        out += [f"{path.name}: {d}" for d in dropped if not d.startswith(_PROJECT_NEVER)]
    try:
        servers = _read_json(root / ".mcp.json").get("mcpServers")
    except SettingsError as e:
        out.append(str(e))
        servers = None
    if isinstance(servers, dict) and servers:
        out.append(
            f".mcp.json: mcpServers ({', '.join(servers)}); each is approved before it first runs"
        )
    return out


def load_settings(
    cwd: Path | None = None,
    environ: dict[str, str] | None = None,
    trust_project: bool = False,
) -> Settings:
    cwd = (cwd or Path.cwd()).resolve()
    root = find_project_root(cwd)
    trusted = trust_project or is_project_trusted(root)
    merged: dict[str, Any] = {}
    sources: list[str] = []
    ignored: list[str] = []
    project_mcp: dict[str, Any] = {}
    mcp_json = root / ".mcp.json"
    if (servers := _read_json(mcp_json).get("mcpServers")) and isinstance(servers, dict):
        if trusted:
            project_mcp.update(servers)
            sources.append(str(mcp_json))
        else:
            ignored.append(f".mcp.json: mcpServers ({', '.join(servers)})")
    for path, from_project in (
        (config_dir() / "settings.json", False),
        (root / ".cmcoder" / "settings.json", True),
        (root / ".cmcoder" / "settings.local.json", True),
    ):
        layer = _strip_managed_only(_read_json(path))
        if layer and from_project:
            own = path.name == "settings.local.json" and _local_is_own(root, path)
            layer, dropped = _filter_project_layer(layer, trusted or own)
            ignored += [f"{path.name}: {d}" for d in dropped]
            if servers := layer.pop("mcpServers", None):
                project_mcp.update(servers if isinstance(servers, dict) else {})
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
    settings.project_trusted = trusted
    settings.ignored_project_settings = ignored
    try:
        settings.project_mcp_servers = {
            name: McpServerConfig.model_validate(cfg) for name, cfg in project_mcp.items()
        }
    except ValueError as e:
        raise SettingsError(f"Invalid MCP server in this project's settings: {e}") from e
    if managed:
        settings.managed_path = str(managed_file)
    return settings


def ignored_settings_message(settings: Settings) -> str | None:
    """A warning when project settings were left out, or None."""
    if not settings.ignored_project_settings:
        return None
    items = "; ".join(settings.ignored_project_settings)
    never = all(
        x.split(": ", 1)[-1].startswith(_PROJECT_NEVER) for x in settings.ignored_project_settings
    )
    if never:
        return (
            f"Ignored in this project's .cmcoder settings: {items}. "
            "Gateways are only read from your user settings (~/.cmcoder/settings.json)."
        )
    return (
        f"Ignored in this project's .cmcoder settings because the folder isn't trusted: {items}. "
        "If you trust this repository, run `cmcoder trust` here."
    )


def add_local_allow_rule(project_root: Path, rule: str) -> Path:
    """Persist an "allow always" answer to .cmcoder/settings.local.json.

    The file's fingerprint is recorded so cmcoder trusts its own rules next
    time, but only if the file was cmcoder's own (or new) before: rules a
    repository committed there must not become trusted by this."""
    path = project_root / ".cmcoder" / "settings.local.json"
    own_before = not path.exists() or _local_is_own(project_root, path)
    data = _read_json(path)
    perms = data.setdefault("permissions", {})
    allow = perms.setdefault("allow", [])
    if rule not in allow:
        allow.append(rule)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    if own_before or is_project_trusted(project_root):
        projects = _read_trust()
        key = _trust_key(project_root)
        entry = _as_dict(projects.get(key))
        projects[key] = {**entry, "local_sha256": _fingerprint(path)}
        _write_trust(projects)
    return path


def update_user_settings(change: Callable[[dict[str, Any]], None]) -> Path:
    """Read ~/.cmcoder/settings.json, apply `change` to it, write it back."""
    path = config_dir() / "settings.json"
    data = _read_json(path)
    change(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path
