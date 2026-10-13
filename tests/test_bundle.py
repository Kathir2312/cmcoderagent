"""The release bundle (packaging/bundle.py): every file or none, the gate
report from each platform's results, checksums that match."""

from __future__ import annotations

import hashlib
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packaging"))

import bundle  # noqa: E402


def artifacts(tmp: Path, skip: str | None = None) -> Path:
    a = tmp / "artifacts"
    for item in bundle.items():
        if item.artifact == skip:
            continue
        name = item.pattern.replace("*", "0.1.0")
        (a / item.artifact).mkdir(parents=True, exist_ok=True)
        (a / item.artifact / name).write_bytes(f"file {item.name}".encode())
    rows = {
        "win32-x64": "Standalone cmcoder, no Python\nVisual Studio extension in Visual Studio\nNetBeans plugin in NetBeans",
        "linux-x64": "Standalone cmcoder, no Python\nVS Code extension in VS Code\nNetBeans plugin in NetBeans",
        "darwin-arm64": "Standalone cmcoder, no Python\nNetBeans plugin in NetBeans",
    }
    for t, text in rows.items():
        (a / f"gate-{t}").mkdir(parents=True)
        (a / f"gate-{t}" / f"gate-{t}.txt").write_text(text + "\n", encoding="utf-8")
    return a


def test_bundle(tmp_path: Path) -> None:
    out = tmp_path / "bundle"
    files = bundle.assemble(artifacts(tmp_path), out)
    assert len(files) == 19
    names = {f.relative_to(out).as_posix() for f in files}
    # For IT: the managed settings (the example, until the company sets its own).
    assert {
        "admin/install-managed.cmd",
        "admin/install-managed.ps1",
        "admin/install-managed.sh",
        "admin/README.txt",
        "admin/managed-settings.example.json",
    } <= names
    assert "visualstudio/cmcoder-visualstudio-win32-x64.vsix" in names
    assert "eclipse/cmcoder-eclipse.zip" in names
    assert "netbeans/cmcoder-netbeans-darwin-arm64.nbm" in names
    assert {f"jetbrains/cmcoder-jetbrains-{t}.zip" for t in bundle.TARGETS} <= names
    assert "terminal/cmcoder-terminal-linux-x64.zip" in names
    readme = (out / "README-FIRST.txt").read_text(encoding="utf-8")
    assert bundle.version() in readme and "NetBeans" in readme and "no Python" in readme
    assert "Install Plugin from Disk" in readme and "PyCharm, Rider" in readme
    assert "admin/README.txt" in readme and "cmcoder setup" in readme
    report = (out / "GATE-REPORT.md").read_text(encoding="utf-8")
    assert "| Standalone cmcoder, no Python | ✅ | ✅ | ✅ |" in report
    assert "| Visual Studio extension in Visual Studio | ✅ | — | — |" in report
    assert "Not covered by automated tests" in report
    assert "Plugin Verifier" in report  # what stands in for Rider
    for line in (out / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", 1)
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == digest


@pytest.mark.parametrize(
    "missing", ["cmcoder-netbeans-linux-x64.nbm", "cmcoder-jetbrains-darwin-arm64.zip"]
)
def test_no_bundle_without_every_file(tmp_path: Path, missing: str) -> None:
    with pytest.raises(SystemExit, match=missing):
        bundle.assemble(artifacts(tmp_path, skip=missing), tmp_path / "bundle")
    assert not (tmp_path / "bundle").exists()


def test_no_bundle_without_gate_results(tmp_path: Path) -> None:
    a = artifacts(tmp_path)
    (a / "gate-darwin-arm64" / "gate-darwin-arm64.txt").unlink()
    with pytest.raises(SystemExit, match="no gate results for darwin-arm64"):
        bundle.assemble(a, tmp_path / "bundle")


def test_the_companys_managed_settings_go_in_the_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    (root / "branding").mkdir(parents=True)
    (root / "branding" / "managed-settings.json").write_text('{"lockProviders": true}')
    shutil.copytree(bundle.ROOT / "packaging" / "admin", root / "packaging" / "admin")
    monkeypatch.setattr(bundle, "ROOT", root)
    files = bundle.admin_files()
    assert files["managed-settings.json"] == root / "branding" / "managed-settings.json"
    assert "managed-settings.example.json" not in files
