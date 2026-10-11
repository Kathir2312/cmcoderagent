"""The user's cmcoder folder (~/.cmcoder; on Windows %USERPROFILE%\\.cmcoder),
made on first use by whichever comes first: the terminal package's installer
or an IDE starting cmcoder.

- settings.json: your company's settings template (branding/
  settings.template.json, filled in once before the build: the gateway, the
  models, its certificate), copied the first time. Never overwritten: from
  then on the file is the developer's.
- settings.example.json: every setting, as a reference; kept up to date.

Nothing here can fail cmcoder: a folder it can't write is left as it is.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import brand
from .config.settings import config_dir

TEMPLATE = "settings.template.json"
EXAMPLE = "settings.example.json"


def _template() -> str | None:
    """The company's template, if there is a valid one (a JSON object)."""
    for folder in brand.folders():
        path = folder / TEMPLATE
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        try:
            if isinstance(json.loads(text), dict):
                return text
        except ValueError:
            return None  # packaging/brand.py rejects it at build time
        return None
    return None


def _example() -> str | None:
    here = Path(__file__).resolve().parent
    for path in (here / EXAMPLE, here.parent.parent / "docs" / EXAMPLE):
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            continue
    return None


def prepare(folder: Path | None = None) -> list[str]:
    """Make the folder and its first files; returns what was created."""
    folder = folder or config_dir()
    made: list[str] = []
    try:
        folder.mkdir(parents=True, exist_ok=True)
        settings = folder / "settings.json"
        template = _template()
        if template is not None and not settings.exists():
            with open(settings, "x", encoding="utf-8") as f:  # never over an existing one
                f.write(template)
            made.append(str(settings))
        example = _example()
        target = folder / EXAMPLE
        if example is not None and (
            not target.exists() or target.read_text(encoding="utf-8") != example
        ):
            target.write_text(example, encoding="utf-8")
            made.append(str(target))
    except (OSError, ValueError):
        pass
    return made
