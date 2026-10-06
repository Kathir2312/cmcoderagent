"""Phase 5 items 2-3: chunking, which files are indexed, keeping the index
current, and the stores (built in, and Chroma at a URL).

The Chroma tests run when CMCODER_TEST_CHROMA_URL points at a Chroma server
(CI starts one). Chroma inside cmcoder (without a URL) isn't offered for now."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from cmcoder.cli.factory import build_provider
from cmcoder.config.settings import RagConfig, Settings
from cmcoder.rag.chunker import MAX_CHARS, MAX_LINES, chunk_file
from cmcoder.rag.files import FileSelector
from cmcoder.rag.index import CodeIndex, collection_name, open_index
from cmcoder.rag.stores import StoreError

from .conftest import API_KEY

CHROMA_URL = os.environ.get("CMCODER_TEST_CHROMA_URL")

AUTH = '''"""Authentication helpers."""


def refresh_auth_token(session):
    """Refresh the expired auth token with the identity provider."""
    session.token = session.provider.renew(session.refresh_token)
    return session.token


def parse_invoice_total(lines):
    """Sum the amounts of an invoice."""
    return sum(float(x.split(";")[1]) for x in lines)
'''


# --- chunking ------------------------------------------------------------------


def test_python_is_split_by_definitions() -> None:
    big_class = "class Big:\n" + "".join(
        f"    def method_{i}(self):\n" + "        x = 1\n" * 30 + "\n" for i in range(4)
    )
    text = AUTH + "\n\n" + big_class
    chunks = chunk_file("app/auth.py", text)
    symbols = [c.symbol for c in chunks]
    assert any(s and "refresh_auth_token" in s for s in symbols)
    assert "Big.method_0" in ",".join(s or "" for s in symbols)
    lines = text.splitlines()
    covered = {n for c in chunks for n in range(c.start_line, c.end_line + 1)}
    assert all(i + 1 in covered for i, line in enumerate(lines) if line.strip())
    assert all(len(c.text) <= MAX_CHARS and c.end_line - c.start_line < MAX_LINES for c in chunks)
    assert all(c.language == "python" for c in chunks)
    assert chunks[0].embedding_text().startswith("File: app/auth.py")


def test_other_languages_and_documents() -> None:
    ts = "\n".join(
        line
        for i in range(3)
        for line in [f"export function handler{i}(req: Request) {{", *["  work();"] * 20, "}", ""]
    )
    chunks = chunk_file("src/api.ts", ts)
    assert [c.symbol for c in chunks] == ["handler0", "handler1", "handler2"]
    md = "# Setup\n\nInstall it.\n" + "text\n" * 20 + "## Usage\n\nRun it.\n" + "more\n" * 20
    assert [c.symbol for c in chunk_file("README.md", md)] == ["Setup", "Usage"]
    assert chunk_file("empty.py", "\n\n") == []
    assert chunk_file("broken.py", "def x(:\n  pass\n" * 3)  # unparsable: still chunked


# --- which files ---------------------------------------------------------------


def test_files_never_indexed(project: Path, tmp_path: Path) -> None:
    (project / "app.py").write_text("x = 1\n")
    (project / ".env").write_text("TOKEN=secret\n")
    (project / "server.key").write_text("KEY\n")
    (project / "secrets").mkdir()
    (project / "secrets" / "db.yaml").write_text("password: x\n")
    (project / "node_modules" / "lib").mkdir(parents=True)
    (project / "node_modules" / "lib" / "i.js").write_text("x\n")
    (project / ".gitignore").write_text("generated/\n")
    (project / "generated").mkdir()
    (project / "generated" / "g.py").write_text("x = 1\n")
    (project / "bundle.min.js").write_text("x\n")
    (project / "logo.png").write_bytes(b"\x89PNG\x00\x00")
    (project / "data.bin.py").write_bytes(b"a\x00b")
    (project / "big.py").write_text("x = 1\n" * 200_000)
    (project / "minified.js").write_text("a" * 5000)
    (tmp_path / "outside.py").write_text("x\n")
    try:
        (project / "link.py").symlink_to(tmp_path / "outside.py")
        (project / "env_link.py").symlink_to(project / ".env")
    except OSError:
        pass  # no symlinks (Windows without the privilege)
    sel = FileSelector(project, RagConfig.model_validate({}))
    files = sorted(sel.rel(p) for p in sel.files() if sel.read(p) is not None)
    assert files == ["app.py"]
    only = FileSelector(project, RagConfig.model_validate({"include": ["src/**"]}))
    assert not only.wanted(project / "app.py")
    no_app = FileSelector(project, RagConfig.model_validate({"exclude": ["app.py"]}))
    assert not no_app.wanted(project / "app.py")


# --- the index -----------------------------------------------------------------


def settings_for(server: Any, store: dict[str, Any] | None = None) -> Settings:
    return Settings.model_validate(
        {
            "providers": {"corp": {"baseUrl": server.base_url, "maxRetries": 0}},
            "model": "corp:qwen3-27b",
            "rag": {"embeddingModel": "corp:text-embedding-3-small", "store": store or {}},
        }
    )


STORES: list[Any] = [
    pytest.param({"type": "local"}, id="local"),
    pytest.param(
        {"type": "chroma", "url": CHROMA_URL},
        id="chroma-server",
        marks=pytest.mark.skipif(not CHROMA_URL, reason="set CMCODER_TEST_CHROMA_URL"),
    ),
]


@pytest.fixture
def code_project(project: Path) -> Path:
    (project / "auth.py").write_text(AUTH)
    (project / "README.md").write_text("# Billing\n\nInvoices are emailed monthly.\n")
    (project / ".env").write_text("TOKEN=topsecret\n")
    return project


@pytest.fixture
def make_index(mock_server: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    monkeypatch.setenv("CMCODER_API_KEY", API_KEY)
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        monkeypatch.delenv(var, raising=False)
    server = mock_server([])
    opened: list[CodeIndex] = []

    def make(root: Path, store: dict[str, Any]) -> tuple[CodeIndex, Any]:
        idx = open_index(settings_for(server, store), root, build_provider)
        assert idx is not None
        opened.append(idx)
        return idx, server

    yield make


@pytest.mark.parametrize("store", STORES)
async def test_index_search_and_stay_current(
    code_project: Path, make_index: Any, store: dict[str, Any]
) -> None:
    idx, server = make_index(code_project, store)
    try:
        await idx.clear()  # a shared test server may hold an earlier run
        result = await idx.update()
        assert result.indexed == 2 and result.chunks >= 2
        hits = await idx.search("refresh the expired auth token", k=2)
        assert hits[0].chunk.path == "auth.py" and "refresh_auth_token" in hits[0].chunk.text
        assert all("topsecret" not in h.chunk.text for h in hits)
        assert 0 < hits[0].score <= 1.0001
        # Nothing changed: nothing embedded again.
        before = len(server.state.embedding_requests)
        again = await idx.update()
        assert again.indexed == 0 and again.unchanged == 2
        assert len(server.state.embedding_requests) == before
        # An edit by the agent is in the next search.
        (code_project / "auth.py").write_text(AUTH + "\n\ndef rotate_signing_keys():\n    pass\n")
        idx.note_changed(code_project / "auth.py")
        hits = await idx.search("rotate signing keys", k=1)
        assert "rotate_signing_keys" in hits[0].chunk.text
        # A file changed by someone else is refreshed when it shows up in results.
        (code_project / "auth.py").write_text(AUTH.replace("renew", "renew_with_mfa"))
        hits = await idx.search("refresh the expired auth token", k=1)
        assert "renew_with_mfa" in hits[0].chunk.text
        # Deleted files leave the index; a path filter narrows the search.
        (code_project / "README.md").unlink()
        assert (await idx.update()).removed == 1
        assert all(h.chunk.path != "README.md" for h in await idx.search("invoices billing", k=5))
        assert await idx.search("token", k=5, path="docs") == []
        status = await idx.status()
        assert status.files == 1 and status.chunks >= 1
    finally:
        await idx.clear()
        await idx.close()


async def test_the_manifest_survives_a_restart(code_project: Path, make_index: Any) -> None:
    idx, server = make_index(code_project, {"type": "local"})
    await idx.update()
    await idx.close()
    again, _ = make_index(code_project, {"type": "local"})
    before = len(server.state.embedding_requests)
    assert again.has_local_index() and (await again.update()).indexed == 0
    assert len(server.state.embedding_requests) == before
    assert (await again.search("parse invoice total", k=1))[0].chunk.symbol
    await again.close()


async def test_read_only_and_errors(code_project: Path, make_index: Any) -> None:
    idx, _ = make_index(code_project, {"type": "local", "readOnly": True})
    with pytest.raises(StoreError, match="read-only"):
        await idx.update()
    await idx.close()
    down, _ = make_index(code_project, {"type": "chroma", "url": "http://127.0.0.1:9"})
    with pytest.raises(StoreError, match="Can't reach the Chroma server.*Is Chroma running"):
        await down.update()
    await down.close()
    # Chroma is always used through its URL (for now).
    with pytest.raises(StoreError, match="Chroma needs its URL"):
        make_index(code_project, {"type": "chroma"})


def test_collection_names(project: Path) -> None:
    (project / ".git" / "config").write_text(
        '[core]\n\tbare = false\n[remote "origin"]\n\turl = https://git.example.com/team/pay-api.git\n'
    )
    name = collection_name(project, "corp:bge-m3")
    assert name.startswith("cmcoder-pay-api-") and len(name) <= 63
    other_clone = project.parent / "elsewhere"
    (other_clone / ".git").mkdir(parents=True)
    (other_clone / ".git" / "config").write_text((project / ".git" / "config").read_text())
    assert collection_name(other_clone, "corp:bge-m3") == name  # same repository: same index
    assert collection_name(project, "corp:other") != name


def test_off_without_a_model(project: Path) -> None:
    assert open_index(Settings.model_validate({}), project, build_provider) is None
    s = Settings.model_validate({"rag": {"enabled": False, "embeddingModel": "x"}})
    assert open_index(s, project, build_provider) is None
    assert sys.version_info >= (3, 11)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
async def test_the_index_is_private(code_project: Path, make_index: Any) -> None:
    import stat

    idx, _ = make_index(code_project, {"type": "local"})
    try:
        await idx.update()
        top = Path(os.environ["CMCODER_CONFIG_DIR"]) / "index"
        for folder in [idx.folder, idx.folder / "local", *idx.folder.parents]:
            assert stat.S_IMODE(folder.stat().st_mode) == 0o700, folder
            if folder == top:
                break
    finally:
        await idx.close()
