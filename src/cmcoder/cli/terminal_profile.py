"""`cmcoder terminal-profile`: a Windows Terminal profile that starts cmcoder,
with the brand's name and icon, installed as a Windows Terminal "fragment"
(%LOCALAPPDATA%/Microsoft/Windows Terminal/Fragments/<name>/cmcoder.json).
Nothing in Windows Terminal's own settings file is changed; removing the
fragment removes the profile."""

from __future__ import annotations

import json
import os
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any

from ..brand import Brand

# Fixed, so reinstalling updates the same profile instead of adding another.
NAMESPACE = uuid.UUID("6a3c1f5e-2f53-4c4e-9a43-3f1d1c6b9e21")


def fragment_file(brand: Brand, environ: dict[str, str] | None = None) -> Path | None:
    env = os.environ if environ is None else environ
    base = env.get("LOCALAPPDATA")
    if not base:
        return None
    folder = "".join(c for c in brand.product_name if c.isalnum() or c in " -_.").strip()
    root = Path(base) / "Microsoft" / "Windows Terminal" / "Fragments"
    return root / (folder or "cmcoder") / "cmcoder.json"


def command_line() -> str:
    """How Windows Terminal should start cmcoder: this executable."""
    if getattr(sys, "frozen", False):
        exe = sys.executable
        args: list[str] = []
    elif found := shutil.which("cmcoder"):
        exe, args = found, []
    else:
        exe, args = sys.executable, ["-m", "cmcoder"]
    return " ".join(f'"{a}"' if " " in a else a for a in [exe, *args])


def profile(brand: Brand) -> dict[str, Any]:
    p: dict[str, Any] = {
        "guid": "{" + str(uuid.uuid5(NAMESPACE, brand.product_name)) + "}",
        "name": brand.product_name,
        "commandline": command_line(),
        "startingDirectory": "%USERPROFILE%",
    }
    icon = brand.icon_ico or brand.icon_png
    if icon is not None:
        p["icon"] = str(icon)
    return {"profiles": [p]}


def install(brand: Brand, environ: dict[str, str] | None = None) -> Path:
    path = fragment_file(brand, environ)
    if path is None:
        raise RuntimeError("Windows Terminal profiles need Windows (LOCALAPPDATA isn't set).")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile(brand), indent=2) + "\n", encoding="utf-8")
    return path


def remove(brand: Brand, environ: dict[str, str] | None = None) -> Path | None:
    path = fragment_file(brand, environ)
    if path is None or not path.exists():
        return None
    path.unlink()
    try:
        path.parent.rmdir()
    except OSError:
        pass
    return path
