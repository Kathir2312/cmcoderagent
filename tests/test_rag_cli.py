"""Phase 5 item 5: `cmcoder rag setup`, `cmcoder index`, `rag status|on|off`
and doctor, as users run them (subprocesses against the mock gateway)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest

from .test_cli import cli
from .test_rag_index import AUTH

CHROMA_URL = os.environ.get("CMCODER_TEST_CHROMA_URL")


@pytest.fixture
def code(project: Path) -> Path:
    (project / "auth.py").write_text(AUTH)
    (project / ".env").write_text("TOKEN=topsecret\n")
    return project


def user_settings() -> dict[str, Any]:
    path = Path(os.environ["CMCODER_CONFIG_DIR"]) / "settings.json"
    return json.loads(path.read_text()) if path.exists() else {}


def test_setup_index_status_off_on(mock_server: Any, code: Path) -> None:
    server = mock_server([])
    r = cli(["rag", "setup", "-m", "default:qwen3-27b", "--yes"], code, server)
    assert r.returncode == 1 and "doesn't work as an embedding model" in r.stderr
    r = cli(
        [
            "rag",
            "setup",
            "-m",
            "default:text-embedding-3-small",
            "--store",
            "local",
            "--index",
            "--yes",
        ],
        code,
        server,
    )
    assert r.returncode == 0, r.stderr
    assert "answers (64 dimensions)" in r.stdout and "Indexed 1 files" in r.stdout
    assert user_settings()["rag"] == {
        "enabled": True,
        "embeddingModel": "default:text-embedding-3-small",
        "store": {"type": "local"},
    }
    r = cli(["index"], code, server)
    assert "Indexed 0 files" in r.stdout and "1 unchanged" in r.stdout
    r = cli(["index", "--status"], code, server)
    assert "1 files" in r.stdout and re.search(r"Automatic context\s+on", r.stdout)
    r = cli(["doctor", "--no-probe"], code, server)
    assert "This project's index: 1 files" in r.stdout
    assert cli(["rag", "off"], code, server).returncode == 0
    assert user_settings()["rag"]["enabled"] is False
    r = cli(["index"], code, server)
    assert r.returncode == 1 and "turned off" in r.stderr
    cli(["rag", "on"], code, server)
    r = cli(["index", "--clear"], code, server)
    assert "Deleted" in r.stdout
    r = cli(["doctor", "--no-probe"], code, server)
    assert "No index for this project yet" in r.stdout


def test_chroma_only_by_url(mock_server: Any, code: Path) -> None:
    """Chroma is always used through its URL (on this PC or a server), for now."""
    server = mock_server([])
    args = ["rag", "setup", "-m", "default:text-embedding-3-small", "--no-index", "--yes"]
    r = cli([*args, "--store", "chroma"], code, server)
    assert r.returncode == 2 and "local, chroma-server" in r.stderr
    r = cli([*args, "--store", "chroma-server"], code, server, {"CMCODER_CHROMA_API_KEY": ""})
    assert r.returncode != 0 and "Chroma needs its URL" in r.stderr


def test_project_scope_never_holds_the_key(mock_server: Any, code: Path) -> None:
    server = mock_server([])
    url = CHROMA_URL or "http://127.0.0.1:9"
    r = cli(
        [
            "rag",
            "setup",
            "-m",
            "default:text-embedding-3-small",
            "--store",
            "chroma-server",
            "--url",
            url,
            "--scope",
            "project",
            "--no-index",
            "--yes",
        ],
        code,
        server,
        {"CMCODER_CHROMA_API_KEY": "chroma-secret-123"},
    )
    if not CHROMA_URL:
        assert r.returncode == 1 and "Can't reach the Chroma server" in r.stderr
        return
    assert r.returncode == 0, r.stderr
    saved = (code / ".cmcoder" / "settings.json").read_text()
    assert url in saved and "chroma-secret-123" not in saved
    assert "trust the project" in r.stdout


def test_not_a_terminal_needs_options(mock_server: Any, code: Path) -> None:
    r = cli(["rag", "setup"], code, mock_server([]))
    assert r.returncode == 2 and "--embedding-model" in r.stderr
