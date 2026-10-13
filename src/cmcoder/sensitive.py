"""Files whose contents are never sent to the model unless an allow rule names them.

Shared by the permission engine (Read/Edit/Grep targets) and the search tools
(so a project-wide Grep can't print a key file's contents either).
"""

from __future__ import annotations

from pathlib import Path

import pathspec

from .config.settings import config_dir

# gitignore syntax, relative to the project root. Patterns without a slash match
# at any depth; "/secrets" and "secrets/**" only at the project root.
SECRET_PATTERNS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa*",
    "id_ed25519*",
    "/secrets",
    "secrets/**",
    ".cmcoder/credentials.json",
)

_SECRET_SPEC = pathspec.GitIgnoreSpec.from_lines(SECRET_PATTERNS)


def _rel(path: Path, root: Path) -> str | None:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return None


def is_secret(path: Path, root: Path) -> bool:
    if path == (config_dir() / "credentials.json").resolve():
        return True
    rel = _rel(path, root) or path.name
    return _SECRET_SPEC.match_file(rel)


def ripgrep_exclude_globs(search_root: Path, project_root: Path) -> list[str]:
    """`--glob !pattern` arguments that keep secret files out of ripgrep results.

    Patterns anchored at the project root only apply when searching from it;
    a search target inside such a folder is refused by the permission check.
    """
    anchored_ok = search_root == project_root
    globs: list[str] = []
    for p in SECRET_PATTERNS:
        if "/" in p and not anchored_ok:
            continue
        globs += ["--glob", "!" + p.lstrip("/")]
    return globs


def safe_project_file(path: Path, root: Path) -> bool:
    """A repository file cmcoder reads by itself (memory, commands, agents, skills):
    it must really be inside the project, and not a secrets file. A symlink to
    `~/.ssh/id_rsa` or to `.env` would otherwise be sent to the model."""
    try:
        resolved, base = path.resolve(), root.resolve()
    except OSError:
        return False
    return resolved.is_relative_to(base) and not is_secret(resolved, base)
