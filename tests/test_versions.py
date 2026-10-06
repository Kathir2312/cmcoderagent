"""Phase 6 item 8: one version number. Every file handed to developers says
the version in pyproject.toml (Eclipse adds its build qualifier; Maven's
-SNAPSHOT is the build's own)."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def first(path: str, pattern: str) -> str:
    text = (ROOT / path).read_text(encoding="utf-8")
    m = re.search(pattern, text, re.MULTILINE)
    assert m, f"no version in {path}"
    return m.group(1)


def base(v: str) -> str:
    """0.1.0.qualifier, 0.1.0-SNAPSHOT, 0.1.0.0 → 0.1.0"""
    v = v.removesuffix("-SNAPSHOT").removesuffix(".qualifier")
    parts = v.split(".")
    return ".".join(parts[:3])


MANIFESTS = {
    "the cmcoder package": lambda: first("src/cmcoder/__init__.py", r'__version__ = "([^"]+)"'),
    "VS Code extension": lambda: json.loads(
        (ROOT / "vscode/package.json").read_text(encoding="utf-8")
    )["version"],
    "JVM core": lambda: first(
        "clients/jvm-core/pom.xml", r"<artifactId>cmcoder-ide-core</artifactId>\s*<version>([^<]+)"
    ),
    "Eclipse plugin": lambda: first(
        "eclipse/plugins/cmcoder.eclipse/META-INF/MANIFEST.MF", r"^Bundle-Version: (\S+)"
    ),
    "Eclipse feature": lambda: first(
        "eclipse/features/cmcoder.eclipse.feature/feature.xml", r'<feature[^>]*?version="([^"]+)"'
    ),
    "Eclipse build": lambda: first(
        "eclipse/pom.xml", r"<artifactId>cmcoder-eclipse-parent</artifactId>\s*<version>([^<]+)"
    ),
    "Visual Studio extension": lambda: first(
        "visualstudio/src/Cmcoder.VisualStudio/source.extension.vsixmanifest",
        r'<Identity [^>]*Version="([^"]+)"',
    ),
    "Visual Studio assembly": lambda: first(
        "visualstudio/src/Cmcoder.VisualStudio/Properties/AssemblyInfo.cs",
        r'AssemblyVersion\("([^"]+)"\)',
    ),
    "NetBeans plugin": lambda: first(
        "netbeans/pom.xml", r"<artifactId>cmcoder-netbeans</artifactId>\s*<version>([^<]+)"
    ),
}


@pytest.mark.parametrize("what", sorted(MANIFESTS))
def test_one_version(what: str) -> None:
    assert base(MANIFESTS[what]()) == VERSION, (
        f"{what} says {MANIFESTS[what]()}, pyproject.toml {VERSION}"
    )
