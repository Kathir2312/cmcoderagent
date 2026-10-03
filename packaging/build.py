"""Build the standalone `cmcoder` (no Python or uv needed) with PyInstaller.

    uv run --with pyinstaller python packaging/build.py

Output: dist/cmcoder/ (a folder: the `cmcoder` executable and its _internal
files). Folder rather than one file: starts fast and is not unpacked to a
temp folder on every run (which antivirus software also dislikes).
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    import PyInstaller.__main__

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
        ]
    )
    exe = out / "cmcoder" / ("cmcoder.exe" if sys.platform == "win32" else "cmcoder")
    print(f"built {exe}")


if __name__ == "__main__":
    main()
