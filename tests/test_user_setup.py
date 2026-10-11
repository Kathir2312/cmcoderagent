"""The user's ~/.cmcoder on first use (src/cmcoder/user_folder.py), the
company's settings files checked at build time (packaging/brand.py), and
`cmcoder setup` (src/cmcoder/cli/setup_cmd.py)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from cmcoder import brand, user_folder
from cmcoder.cli.main import sub_app
from cmcoder.config.settings import config_dir
from cmcoder.providers.auth import load_stored_api_key

from .conftest import API_KEY

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packaging"))

import brand as build_brand  # noqa: E402  (packaging/brand.py)

TEMPLATE = {
    "providers": {"corp": {"baseUrl": "https://ai.corp.test/v1"}},
    "model": "corp:qwen3-27b",
}


@pytest.fixture
def company(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A build with the company's settings template in it."""
    folder = tmp_path / "_brand"
    folder.mkdir()
    (folder / user_folder.TEMPLATE).write_text(json.dumps(TEMPLATE), encoding="utf-8")
    monkeypatch.setattr(brand, "folders", lambda: [folder])
    return folder


def test_the_first_start_makes_the_folder_with_the_companys_settings(company: Path) -> None:
    home = config_dir() / "fresh"
    made = user_folder.prepare(home)
    assert json.loads((home / "settings.json").read_text(encoding="utf-8")) == TEMPLATE
    example = (ROOT / "docs" / "settings.example.json").read_text(encoding="utf-8")
    assert (home / "settings.example.json").read_text(encoding="utf-8") == example
    assert made == [str(home / "settings.json"), str(home / "settings.example.json")]
    assert user_folder.prepare(home) == []  # nothing new the second time


def test_the_developers_settings_are_never_overwritten(company: Path) -> None:
    home = config_dir()
    (home / "settings.json").write_text('{"model": "mine"}', encoding="utf-8")
    (home / "settings.example.json").write_text("an old example", encoding="utf-8")
    made = user_folder.prepare(home)
    assert (home / "settings.json").read_text(encoding="utf-8") == '{"model": "mine"}'
    # The reference file is cmcoder's, kept up to date.
    assert made == [str(home / "settings.example.json")]
    # Deleting settings.json brings the template back on the next start.
    (home / "settings.json").unlink()
    user_folder.prepare(home)
    assert json.loads((home / "settings.json").read_text(encoding="utf-8")) == TEMPLATE


def test_no_template_or_a_broken_one_makes_no_settings(
    company: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (company / user_folder.TEMPLATE).write_text("[1, 2]", encoding="utf-8")
    user_folder.prepare(tmp_path / "a")
    assert not (tmp_path / "a" / "settings.json").exists()
    assert (tmp_path / "a" / "settings.example.json").exists()
    monkeypatch.setattr(brand, "folders", lambda: [tmp_path / "nothing"])
    user_folder.prepare(tmp_path / "b")
    assert not (tmp_path / "b" / "settings.json").exists()


def test_a_folder_it_cant_make_never_stops_cmcoder(company: Path, tmp_path: Path) -> None:
    blocked = tmp_path / "a-file"
    blocked.write_text("")
    assert user_folder.prepare(blocked / ".cmcoder") == []


def test_the_build_refuses_broken_company_settings(tmp_path: Path) -> None:
    def check(data: Any, managed: bool = False) -> list[str]:
        path = tmp_path / ("managed-settings.json" if managed else "settings.template.json")
        path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
        return build_brand.check_settings(path, managed)

    assert build_brand.check_settings(tmp_path / "missing.json", False) == []
    assert check(TEMPLATE) == []
    assert check("﻿" + json.dumps(TEMPLATE)) == []  # saved by Notepad
    assert "invalid JSON" in check("{")[0]
    assert "JSON object" in check([1])[0]
    assert (
        "example address"
        in check({"providers": {"corp": {"baseUrl": "https://YOUR-GATEWAY.example/v1"}}})[0]
    )
    assert check({"permissions": {"defaultMode": "sideways"}})  # not a valid setting
    rule = {"permissions": {"disableBypassPermissionsMode": "disable"}}
    assert "only counts in managed-settings.json" in check(rule)[0]
    assert check(rule, managed=True) == []
    managed_example = (ROOT / "docs" / "managed-settings.example.json").read_text("utf-8")
    assert "example address" in check(managed_example, managed=True)[0]


def run_setup(
    args: list[str], monkeypatch: pytest.MonkeyPatch, key: str = API_KEY, answers: str = ""
) -> Any:
    monkeypatch.setattr("getpass.getpass", lambda prompt="": key)
    return CliRunner().invoke(sub_app, ["setup", *args], input=answers)


def settings_file() -> dict[str, Any]:
    return json.loads((config_dir() / "settings.json").read_text(encoding="utf-8"))


def test_setup_from_a_script(mock_server: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    server = mock_server([])
    (config_dir() / "settings.json").write_text('{"autoCompact": false}', encoding="utf-8")
    result = run_setup(
        ["--gateway", server.base_url + "/", "--model", "qwen3-27b", "--small-model", "qwen3-7b"],
        monkeypatch,
    )
    assert result.exit_code == 0, result.output
    assert "The gateway answers: 2 models." in result.output
    assert settings_file() == {
        "autoCompact": False,  # what was there is kept
        "providers": {"corp": {"baseUrl": server.base_url}},
        "model": "corp:qwen3-27b",
        "smallFastModel": "corp:qwen3-7b",
    }
    assert load_stored_api_key("corp") == API_KEY
    assert API_KEY not in result.output
    assert "cmcoder doctor" in result.output


def test_setup_by_answering_questions(mock_server: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    server = mock_server([], models=["qwen3-27b", "qwen3-7b", "qwen3-vl"])
    # The address, the type, the main model by number, Enter (no small model),
    # the vision model by name.
    answers = f"{server.base_url}\nopenai\n1\n\nqwen3-vl\n"
    result = run_setup([], monkeypatch, answers=answers)
    assert result.exit_code == 0, result.output
    data = settings_file()
    assert data["model"] == "corp:qwen3-27b"
    assert data["visionModel"] == "corp:qwen3-vl"
    assert "smallFastModel" not in data
    # Run again: Enter keeps every answer (model names with ":" stay whole).
    data["model"] = "corp:qwen3:8b"
    (config_dir() / "settings.json").write_text(json.dumps(data), encoding="utf-8")
    again = run_setup([], monkeypatch, key="", answers="\n\n\n\n\n")
    assert again.exit_code == 0, again.output
    assert settings_file() == data


def test_setup_refuses_plain_http_to_another_computer(monkeypatch: pytest.MonkeyPatch) -> None:
    result = run_setup(["--gateway", "http://ai.corp.test/v1", "--model", "m"], monkeypatch)
    assert result.exit_code == 2
    assert "https://" in result.output
    assert not (config_dir() / "settings.json").exists()
