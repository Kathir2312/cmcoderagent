from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from cmcoder.providers.auth import ApiKeyAuth
from cmcoder.providers.openai_compat import OpenAICompatProvider
from cmcoder.providers.transport import TransportOptions, build_client
from cmcoder.testing.mock_server import MockServer, MockState
from cmcoder.tools.base import ToolContext

API_KEY = "sk-test-key"


@pytest.fixture(autouse=True)
def _isolate_config(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never touch the real ~/.cmcoder or keychain; ignore the developer's env."""
    monkeypatch.setenv("CMCODER_CONFIG_DIR", str(tmp_path_factory.mktemp("cmcoder-config")))
    for var in list(os.environ):
        if var.startswith(("CMCODER_", "OPENAI_")) and var != "CMCODER_CONFIG_DIR":
            monkeypatch.delenv(var)
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")
    # Never pick up real managed settings from the machine running the tests.
    missing = tmp_path_factory.mktemp("no-managed") / "managed-settings.json"
    monkeypatch.setattr("cmcoder.config.settings.managed_settings_path", lambda: missing)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / ".git").mkdir()
    return root


@pytest.fixture
def ctx(project: Path) -> Iterator[ToolContext]:
    c = ToolContext(cwd=project, project_root=project)
    yield c


def start_mock(script: list[dict[str, Any]], **kw: Any) -> MockServer:
    server = MockServer(MockState(script, api_key=API_KEY, **kw))
    server.__enter__()
    return server


@pytest.fixture
def mock_server() -> Iterator[Any]:
    servers: list[MockServer] = []

    def make(script: list[dict[str, Any]], **kw: Any) -> MockServer:
        s = start_mock(script, **kw)
        servers.append(s)
        return s

    yield make
    for s in servers:
        s.__exit__(None, None, None)


def make_provider(
    server: MockServer, key: str = API_KEY, max_retries: int = 2
) -> OpenAICompatProvider:
    return OpenAICompatProvider(
        "mock",
        server.base_url,
        ApiKeyAuth("mock", explicit_key=key),
        build_client(TransportOptions(read_timeout=10)),
        max_retries=max_retries,
    )
