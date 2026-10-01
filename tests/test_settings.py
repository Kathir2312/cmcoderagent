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
