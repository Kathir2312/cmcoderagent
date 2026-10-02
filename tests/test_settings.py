from __future__ import annotations

import json
from pathlib import Path

import pytest

from cmcoder.config.settings import SettingsError, add_local_allow_rule, load_settings


def write(path: Path, data: dict) -> None:  # type: ignore[type-arg]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def test_layers_merge(project: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    user = tmp_path / "user"
    monkeypatch.setenv("CMCODER_CONFIG_DIR", str(user))
    write(
        user / "settings.json",
        {
            "providers": {"corp": {"baseUrl": "https://ai.example/v1", "caCertPath": "/ca.pem"}},
            "model": "corp:qwen3-27b",
            "permissions": {"allow": ["Bash(npm test:*)"]},
        },
    )
    write(
        project / ".cmcoder/settings.json",
        {"permissions": {"allow": ["Bash(make:*)"], "defaultMode": "acceptEdits"}},
    )
    write(project / ".cmcoder/settings.local.json", {"smallFastModel": "corp:qwen3-7b"})
    (project / "sub").mkdir()
    s = load_settings(project / "sub", environ={})
    assert s.providers["corp"].ca_cert_path == "/ca.pem"
    assert s.permissions.allow == ["Bash(npm test:*)", "Bash(make:*)"]
    assert s.permissions.default_mode == "acceptEdits"
    assert s.resolve_model() == ("corp", "qwen3-27b")
    assert s.resolve_model(s.small_fast_model) == ("corp", "qwen3-7b")
    assert len(s.sources) == 3


def test_env_vars(project: Path) -> None:
    s = load_settings(
        project,
        environ={
            "CMCODER_BASE_URL": "https://gw/v1",
            "CMCODER_MODEL": "qwen3:8b",
            "CMCODER_CUSTOM_HEADERS": "X-Team: core; X-Env: dev",
        },
    )
    assert s.providers["default"].base_url == "https://gw/v1"
    assert s.providers["default"].headers == {"X-Team": "core", "X-Env": "dev"}
    # A colon in the model name is not a provider prefix.
    assert s.resolve_model() == ("default", "qwen3:8b")


def test_openai_fallback_env(project: Path) -> None:
    s = load_settings(
        project, environ={"OPENAI_BASE_URL": "https://api.openai.com/v1", "CMCODER_MODEL": "gpt-x"}
    )
    assert s.providers["default"].base_url == "https://api.openai.com/v1"


def test_missing_config_errors(project: Path) -> None:
    s = load_settings(project, environ={})
    with pytest.raises(SettingsError, match="No model configured"):
        s.resolve_model()


def test_invalid_json(project: Path) -> None:
    (project / ".cmcoder").mkdir()
    (project / ".cmcoder/settings.json").write_text("{nope")
    with pytest.raises(SettingsError, match="Invalid JSON"):
        load_settings(project, environ={})


def test_add_local_allow_rule(project: Path) -> None:
    add_local_allow_rule(project, "Bash(pytest:*)")
    add_local_allow_rule(project, "Bash(pytest:*)")
    data = json.loads((project / ".cmcoder/settings.local.json").read_text())
    assert data == {"permissions": {"allow": ["Bash(pytest:*)"]}}


def test_openai_api_key_is_not_sent_to_other_gateways() -> None:
    from cmcoder.config.settings import env_api_key_source

    # An unrelated OpenAI key must not go to the company gateway.
    assert env_api_key_source({"OPENAI_API_KEY": "sk-openai"}) == (None, None)
    assert env_api_key_source(
        {"OPENAI_API_KEY": "sk-openai", "CMCODER_BASE_URL": "https://gw/v1"}
    ) == (None, None)
    # Only when the endpoint itself comes from OPENAI_BASE_URL.
    assert env_api_key_source(
        {"OPENAI_API_KEY": "sk-o", "OPENAI_BASE_URL": "https://api.openai.com/v1"}
    ) == ("sk-o", "OPENAI_API_KEY")
    assert env_api_key_source({"CMCODER_API_KEY": "sk-c", "OPENAI_API_KEY": "sk-o"}) == (
        "sk-c",
        "CMCODER_API_KEY",
    )


def test_model_inside_providers_gets_a_hint(project: Path) -> None:
    write(
        project / ".cmcoder/settings.json",
        {
            "providers": {
                "corp": {"baseUrl": "https://gw/v1"},
                "model": "corp:x",
                "smallFastModel": "corp:y",
            },
        },
    )
    with pytest.raises(SettingsError, match="go at the top level"):
        load_settings(project, environ={})


def test_high_risk_deny_cannot_be_relaxed_by_project(
    project: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    user = tmp_path / "user"
    monkeypatch.setenv("CMCODER_CONFIG_DIR", str(user))
    write(
        user / "settings.json",
        {
            "providers": {"corp": {"baseUrl": "https://ai.example/v1"}},
            "permissions": {"highRiskCommands": "deny"},
        },
    )
    write(project / ".cmcoder/settings.json", {"permissions": {"highRiskCommands": "ask"}})
    assert load_settings(project, environ={}).permissions.high_risk_commands == "deny"
    write(user / "settings.json", {"providers": {"corp": {"baseUrl": "https://ai.example/v1"}}})
    assert load_settings(project, environ={}).permissions.high_risk_commands == "ask"


def test_project_root_never_grows_too_wide(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Hands-on test on Windows: a stray .cmcoder high up made E:\\ the project
    root, so the whole drive counted as "inside the project"."""
    from cmcoder.config.settings import find_project_root

    home = tmp_path / "home"
    work = home / "work" / "app" / "src"
    work.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("CMCODER_CONFIG_DIR", str(home / ".cmcoder"))
    (home / ".cmcoder").mkdir()  # the user's own config folder
    assert find_project_root(work) == work  # not the home folder

    (home / "work" / ".cmcoder").mkdir()  # a real project marker still counts
    assert find_project_root(work) == home / "work"
    (home / "work" / "app" / ".git").mkdir()  # the nearest marker wins
    assert find_project_root(work) == home / "work" / "app"


def test_drive_root_is_never_the_project_root() -> None:
    from cmcoder.config.settings import find_project_root

    root = Path(Path.cwd().anchor)
    assert find_project_root(root) == root  # stays where you are, never wider
