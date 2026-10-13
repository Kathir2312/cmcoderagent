"""Every relative link in the repository's Markdown points at something that
exists, and every #section at a heading that's there. docs/ is split by
audience (docs/README.md); pages move, and a move must not leave dead links."""

from __future__ import annotations

import os
import re
import subprocess
import unicodedata
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LINK = re.compile(r"\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
FENCE = re.compile(r"^\s*(```|~~~)")
# Linked before they exist: the IntelliJ guide's screenshots, added by hand from
# the Release build's gate-screenshots-* artifact (docs/project/phases/phase7/PLAN.md).
NOT_YET = {
    "docs/guides/screenshots/intellij-1-chat.png",
    "docs/guides/screenshots/intellij-2-diff.png",
    "docs/guides/screenshots/intellij-3-navigator.png",
}


def markdown_files() -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "*.md"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.split("\n")
        files = [ROOT / f for f in out if f]
    except (OSError, subprocess.CalledProcessError):  # not a git clone
        files = list(ROOT.rglob("*.md"))
    skip = ("node_modules", ".venv", "evals/.runs", "evals/tasks", "/target/", "/build/", "/dist/")
    return [f for f in files if f.is_file() and not any(s in f.as_posix() for s in skip)]


def prose_lines(text: str) -> list[str]:
    """The lines outside fenced code blocks."""
    lines, fenced = [], False
    for line in text.splitlines():
        if FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            lines.append(line)
    return lines


def slug(heading: str) -> str:
    """GitHub's anchor for a heading."""
    text = re.sub(r"`([^`]*)`", r"\1", heading.strip())
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # a link: its text
    text = text.lower()
    kept = "".join(c for c in text if c in " -_" or unicodedata.category(c)[0] in ("L", "N"))
    return kept.replace(" ", "-")


@cache
def anchors(page: Path) -> frozenset[str]:
    seen: dict[str, int] = {}
    out = set()
    for line in prose_lines(page.read_text("utf-8")):
        m = re.match(r"^#{1,6}\s+(.*?)\s*#*\s*$", line)
        if not m:
            continue
        base = slug(m.group(1))
        n = seen.get(base, 0)
        seen[base] = n + 1
        out.add(base if n == 0 else f"{base}-{n}")
    return frozenset(out)


def test_relative_links_and_anchors_resolve() -> None:
    broken = []
    for page in markdown_files():
        for line in prose_lines(page.read_text("utf-8")):
            for target in LINK.findall(line):
                if re.match(r"^[a-z][a-z0-9+.-]*:", target) or target.startswith("<"):
                    continue
                path, _, anchor = target.partition("#")
                dest = (page.parent / path).resolve() if path else page
                where = f"{page.relative_to(ROOT)}: {target}"
                if not dest.exists():
                    if Path(os.path.relpath(dest, ROOT)).as_posix() not in NOT_YET:
                        broken.append(f"{where} (no such file)")
                elif anchor and dest.suffix == ".md" and anchor not in anchors(dest):
                    broken.append(f"{where} (no such section)")
    assert not broken, "dead links:\n" + "\n".join(broken)


def test_not_yet_is_still_missing() -> None:
    there = [f for f in sorted(NOT_YET) if (ROOT / f).exists()]
    assert not there, f"now in the repository, take them out of NOT_YET: {there}"


def test_slug_matches_github() -> None:
    assert slug("1. The build machine") == "1-the-build-machine"
    assert slug("Before the first build: your company's details") == (
        "before-the-first-build-your-companys-details"
    )
    assert slug("The quick way: one command") == "the-quick-way-one-command"
    assert slug("`cmcoder rag setup` (terminal)") == "cmcoder-rag-setup-terminal"
