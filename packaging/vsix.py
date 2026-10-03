"""Package the VS Code extension for this platform, with the standalone
`cmcoder` (from packaging/build.py) inside, so users need nothing else.

    uv run python packaging/vsix.py          # after packaging/build.py

Output: vscode/cmcoder-<target>.vsix (e.g. win32-x64, linux-x64, darwin-arm64).
The extension uses the bundled cmcoder unless cmcoder.executable is set.
Needs Node.js 22 (the packaging tool).
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXT = ROOT / "vscode"


def target() -> str:
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x64"
    system = {"win32": "win32", "darwin": "darwin"}.get(sys.platform, "linux")
    return f"{system}-{arch}"


def main() -> None:
    built = ROOT / "dist" / "cmcoder"
    if not built.is_dir():
        sys.exit("dist/cmcoder is missing: run packaging/build.py first")
    bundled = EXT / "bin" / "cmcoder"
    shutil.rmtree(bundled, ignore_errors=True)
    shutil.copytree(built, bundled, symlinks=True)  # keeps the executable bits
    npx = shutil.which("npx") or shutil.which("npx.cmd")
    if npx is None:
        sys.exit("npx (Node.js) is needed to package the extension")
    t = target()
    out = f"cmcoder-{t}.vsix"
    node = shutil.which("node") or "node"
    subprocess.run(
        [node, "esbuild.mjs", "--production"], cwd=EXT, check=True, stdin=subprocess.DEVNULL
    )
    subprocess.run(
        [npx, "vsce", "package", "--target", t, "--skip-license", "--out", out],
        cwd=EXT,
        check=True,
        stdin=subprocess.DEVNULL,
    )
    print(f"built {EXT / out}")


if __name__ == "__main__":
    main()
