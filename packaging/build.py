"""Build the standalone `cmcoder` (no Python or uv needed) with PyInstaller.

    uv run --with pyinstaller python packaging/build.py

Output: dist/cmcoder/ (a folder: the `cmcoder` executable and its _internal
files). Folder rather than one file: starts fast and is not unpacked to a
temp folder on every run (which antivirus software also dislikes).

Branding (branding/, checked by packaging/brand.py) goes in as
cmcoder/_brand; on Windows, cmcoder.exe also gets the icon and file details.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packaging"))

import brand  # noqa: E402


def main() -> None:
    import PyInstaller.__main__

    try:
        brand.generate()
    except brand.BrandError as e:
        sys.exit(str(e))
    generated = brand.OUT
    data = [brand.BRANDING / n for n in ("brand.json", "logo.txt", "icon.png")]
    data.append(generated / "cmcoder.ico")
    add_data = [f"--add-data={p}{os.pathsep}cmcoder/_brand" for p in data if p.is_file()]
    windows = []
    if sys.platform == "win32":
        windows = [
            f"--icon={generated / 'cmcoder.ico'}",
            f"--version-file={generated / 'version-info.txt'}",
        ]
    out = ROOT / "dist"
    shutil.rmtree(out / "cmcoder", ignore_errors=True)
    PyInstaller.__main__.run(
        [
            str(ROOT / "packaging" / "cmcoder_entry.py"),
            "--name=cmcoder",
            "--onedir",
            "--console",
            "--noconfirm",
            "--clean",
            f"--distpath={out}",
            f"--workpath={ROOT / 'build' / 'pyinstaller'}",
            f"--specpath={ROOT / 'build'}",
            # Imported lazily or found through entry points / package data:
            "--collect-submodules=cmcoder",
            "--collect-submodules=mcp",
            "--collect-submodules=keyring",
            "--copy-metadata=keyring",
            "--collect-data=textual",
            "--collect-submodules=truststore",
            *add_data,
            *windows,
        ]
    )
    exe = out / "cmcoder" / ("cmcoder.exe" if sys.platform == "win32" else "cmcoder")
    print(f"built {exe}")


if __name__ == "__main__":
    main()
