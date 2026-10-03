"""Managed settings: the organisation's rules, which users and projects can't loosen."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from cmcoder.cli.doctor import Doctor
from cmcoder.cli.factory import AgentOptions, build_agent
from cmcoder.config import settings as settings_mod
from cmcoder.config.settings import SettingsError, load_settings
from cmcoder.core.agent import Agent, PermissionAnswer, PermissionRequest
from cmcoder.core.permissions import ModeNotAllowed, PermissionPolicy
from cmcoder.providers.profiles import resolve_profile
from cmcoder.tools.base import ToolContext
from cmcoder.tools.registry import default_tools

from .conftest import make_provider

# The real function, captured before the conftest replaces it in each test.
REAL_PATH = settings_mod.managed_settings_path

GATEWAY = {"corp": {"baseUrl": "https://gateway.example/v1", "caCertPath": "/etc/corp-ca.pem"}}
NO_NETWORK = {"corp": {"baseUrl": "http://127.0.0.1:9/v1"}}  # never reached in these tests


@pytest.fixture
def managed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Write the managed settings file (where an admin would put it)."""
    path = tmp_path / "admin" / "managed-settings.json"
    monkeypatch.setattr(settings_mod, "managed_settings_path", lambda: path)

    def write(data: Any) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data if isinstance(data, str) else json.dumps(data))
        return path

    return write


@pytest.fixture
def user_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    user = tmp_path / "user"
    monkeypatch.setenv("CMCODER_CONFIG_DIR", str(user))

    def write(data: dict[str, Any]) -> None:
        user.mkdir(exist_ok=True)
        (user / "settings.json").write_text(json.dumps(data))

    return write


def project_settings(project: Path, data: dict[str, Any]) -> None:
    (project / ".cmcoder").mkdir(exist_ok=True)
    (project / ".cmcoder" / "settings.json").write_text(json.dumps(data))


# --- the file ------------------------------------------------------------------------


def test_no_managed_file(project: Path, managed: Any) -> None:
    s = load_settings(project, environ={})
    assert s.managed_path is None


@pytest.mark.parametrize(
    ("content", "message"),
    [("{not json", "not valid JSON"), ("[1, 2]", "must contain a JSON object")],
)
def test_broken_file_stops_cmcoder(project: Path, managed: Any, content: str, message: str) -> None:
    managed(content)
    with pytest.raises(SettingsError, match=message):
        load_settings(project, environ={})


def test_unreadable_file_stops_cmcoder(project: Path, managed: Any) -> None:
    path = managed({})
    path.unlink()
    path.mkdir()  # exists, but can't be read as a file
    with pytest.raises(SettingsError, match="can't be read"):
        load_settings(project, environ={})


def test_location_cannot_be_moved_by_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    real = REAL_PATH
    monkeypatch.setenv("CMCODER_CONFIG_DIR", "/tmp/elsewhere")
    monkeypatch.setenv("ProgramFiles", "/tmp/evil")
    monkeypatch.setenv("PROGRAMDATA", "/tmp/evil")
    expected = {
        "win32": "Program Files/cmcoder/managed-settings.json",
        "darwin": "/Library/Application Support/cmcoder/managed-settings.json",
    }.get(sys.platform, "/etc/cmcoder/managed-settings.json")
    assert real().as_posix().endswith(expected)
    assert "evil" not in str(real()) and "elsewhere" not in str(real())


def test_windows_path_comes_from_windows_not_env(monkeypatch: pytest.MonkeyPatch) -> None:
    real = REAL_PATH
    monkeypatch.setattr(settings_mod.sys, "platform", "win32")
    monkeypatch.setenv("ProgramFiles", "D:/evil")
    path = real().as_posix()
    assert path.endswith("Program Files/cmcoder/managed-settings.json")
    assert "evil" not in path and "ProgramData" not in path


# --- what it enforces ----------------------------------------------------------------


def test_deny_rules_and_high_risk_always_apply(
    project: Path, managed: Any, user_settings: Any
) -> None:
    managed({"permissions": {"deny": ["Bash(curl:*)"], "highRiskCommands": "deny"}})
    user_settings({"permissions": {"deny": [], "allow": ["Bash(curl:*)"]}})
    project_settings(project, {"permissions": {"highRiskCommands": "ask"}})
    s = load_settings(project, environ={})
    assert "Bash(curl:*)" in s.permissions.deny
    assert s.permissions.high_risk_commands == "deny"
    assert s.managed_path and s.sources[-1].endswith("(managed)")


def test_managed_only_keys_are_ignored_elsewhere(
    project: Path, managed: Any, user_settings: Any
) -> None:
    """A project file can't speak for the organisation (e.g. lock you to its own gateway)."""
    project_settings(
        project,
        {
            "lockProviders": True,
            "providers": {"evil": {"baseUrl": "https://evil.example/v1"}},
            "permissions": {"allowManagedPermissionRulesOnly": True, "allow": ["Read"]},
        },
    )
    user_settings({"providers": GATEWAY, "permissions": {"allow": ["Bash(npm test:*)"]}})
    s = load_settings(project, environ={})
    assert s.lock_providers is False
    assert s.permissions.allow_managed_permission_rules_only is False
    # Security review: a project never adds or redirects gateways (see test_project_trust.py).
    assert set(s.providers) == {"corp"}
    assert "Bash(npm test:*)" in s.permissions.allow


def test_only_managed_allow_rules(project: Path, managed: Any, user_settings: Any) -> None:
    managed(
        {
            "permissions": {
                "allowManagedPermissionRulesOnly": True,
                "allow": ["Bash(npm test:*)"],
            }
        }
    )
    user_settings({"providers": GATEWAY, "model": "corp:qwen3-27b"})
    project_settings(project, {"permissions": {"allow": ["Bash", "Edit(**)"]}})
    (project / ".cmcoder" / "settings.local.json").write_text(
        json.dumps({"permissions": {"allow": ["Bash(rm:*)"]}})
    )
    s = load_settings(project, environ={})
    assert s.permissions.allow == ["Bash(npm test:*)"]


async def test_only_managed_allow_rules_reach_the_agent(
    project: Path, managed: Any, user_settings: Any, mock_server: Any
) -> None:
    server = mock_server([])
    managed({"permissions": {"allowManagedPermissionRulesOnly": True, "allow": ["Read"]}})
    user_settings({"providers": {"corp": {"baseUrl": server.base_url}}, "model": "corp:qwen3-27b"})
    s = load_settings(project, environ={})
    opts = AgentOptions(cwd=project, allowed_tools=["Bash"], disallowed_tools=["Bash(git push:*)"])
    agent = await build_agent(s, opts)
    try:
        assert [str(r) for r in agent.policy.allow] == ["Read"]  # --allowedTools ignored
        assert "Bash(git push:*)" in [str(r) for r in agent.policy.deny]  # deny still adds
        agent.policy.add_allow("Bash(make:*)")  # "always allow" answers don't stick
        assert [str(r) for r in agent.policy.allow] == ["Read"]
    finally:
        await agent.close()


async def test_always_allow_is_not_offered(mock_server: Any, project: Path) -> None:
    server = mock_server(
        [
            {"tool_calls": [{"name": "Bash", "arguments": {"command": "make"}}]},
            {"content": "ok"},
        ]
    )
    asked: list[PermissionRequest] = []
    saved: list[str] = []

    async def ask(req: PermissionRequest) -> PermissionAnswer:
        asked.append(req)
        return PermissionAnswer(allow=True, remember=True)

    agent = Agent(
        make_provider(server),
        "qwen3-27b",
        resolve_profile("qwen3-27b"),
        default_tools(),
        PermissionPolicy(allow_rules_locked=True),
        ToolContext(cwd=project, project_root=project),
        "test",
        ask=ask,
        on_rule_saved=saved.append,
    )
    try:
        [e async for e in agent.run("build")]
    finally:
        await agent.close()
    assert asked and not asked[0].can_remember
    assert saved == []


@pytest.mark.parametrize("where", ["flag", "user settings", "project settings"])
async def test_bypass_mode_can_be_disabled(
    project: Path, managed: Any, user_settings: Any, where: str
) -> None:
    managed({"permissions": {"disableBypassPermissionsMode": "disable"}})
    user: dict[str, Any] = {"providers": NO_NETWORK, "model": "corp:qwen3-27b"}
    if where == "user settings":
        user["permissions"] = {"defaultMode": "bypassPermissions"}
    user_settings(user)
    if where == "project settings":
        project_settings(project, {"permissions": {"defaultMode": "bypassPermissions"}})
    # A trusted project, so its bypass mode is used and then refused (an untrusted
    # project's bypass mode is ignored before it gets here).
    s = load_settings(project, environ={}, trust_project=True)
    opts = AgentOptions(
        cwd=project, permission_mode="bypassPermissions" if where == "flag" else None
    )
    with pytest.raises(SettingsError, match="disabled by your organisation"):
        await build_agent(s, opts)


def test_bypass_mode_cannot_be_switched_on_later() -> None:
    policy = PermissionPolicy("default", bypass_disabled=True)
    with pytest.raises(ModeNotAllowed):
        policy.mode = "bypassPermissions"  # /mode bypassPermissions
    assert policy.available_modes() == ["default", "acceptEdits", "plan"]  # Shift+Tab cycle
    assert policy.mode == "default"


def test_providers_can_be_locked(project: Path, managed: Any, user_settings: Any) -> None:
    managed({"lockProviders": True, "providers": GATEWAY})
    user_settings({"providers": {"home": {"baseUrl": "https://other.example/v1"}}})
    env = {"CMCODER_BASE_URL": "https://exfil.example/v1", "CMCODER_MODEL": "corp:qwen3-7b"}
    s = load_settings(project, environ=env)
    assert set(s.providers) == {"corp"}
    assert s.providers["corp"].ca_cert_path == "/etc/corp-ca.pem"
    assert s.resolve_model() == ("corp", "qwen3-7b")  # CMCODER_MODEL still chooses the model
    # The user's own provider is gone: "home:..." is just a model name on the locked gateway.
    assert s.resolve_model("home:qwen3-27b")[0] == "corp"


def test_lock_without_providers_is_an_error(project: Path, managed: Any) -> None:
    managed({"lockProviders": True})
    with pytest.raises(SettingsError, match="define no providers"):
        load_settings(project, environ={})


def test_managed_env_wins(project: Path, managed: Any, user_settings: Any) -> None:
    managed({"env": {"HTTPS_PROXY": "http://proxy.corp:8080"}})
    user_settings({"env": {"HTTPS_PROXY": "", "FOO": "bar"}})
    s = load_settings(project, environ={})
    assert s.env == {"HTTPS_PROXY": "http://proxy.corp:8080", "FOO": "bar"}


def test_doctor_reports_managed_settings(project: Path, managed: Any) -> None:
    managed(
        {
            "lockProviders": True,
            "providers": GATEWAY,
            "permissions": {"disableBypassPermissionsMode": "disable", "deny": ["Bash(curl:*)"]},
        }
    )
    console = Console(record=True, width=200)
    Doctor(load_settings(project, environ={}), console).check_managed()
    out = console.export_text()
    assert "Managed settings in effect" in out
    assert "bypassPermissions disabled" in out and "providers locked to: corp" in out
    # The test's temp folder is writable by us, which doctor must flag.
    assert "can be changed by the current user" in out
