"""Package the VS Code extension for this platform, with the standalone
`cmcoder` (from packaging/build.py) inside, so users need nothing else.

    uv run python packaging/vsix.py      # after packaging/build.py

Output: vscode/cmcoder-<target>.vsix (e.g. win32-x64, linux-x64, darwin-arm64).
The extension uses the bundled cmcoder unless cmcoder.executable is set.
Branding (branding/): the icons and the name and publisher in package.json
are applied for the packaging only; the files are put back afterwards.
Needs Node.js 22 (the packaging tool).
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXT = ROOT / "vscode"
sys.path.insert(0, str(ROOT / "packaging"))

import brand  # noqa: E402


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
    # Links copied as what they point to: a .vsix (zip) can't hold links, and
    # vsce's secret scan fails on a link to a folder (macOS: Python.framework).
    # copy2 keeps the executable bits.
    shutil.copytree(built, bundled, symlinks=False)
    npx = shutil.which("npx") or shutil.which("npx.cmd")
    if npx is None:
        sys.exit("npx (Node.js) is needed to package the extension")
    try:
        b = brand.generate()
    except brand.BrandError as e:
        sys.exit(str(e))
    t = target()
    out = f"cmcoder-{t}.vsix"
    node = shutil.which("node") or "node"
    package_json = EXT / "package.json"
    branded = {
        EXT / "media" / "icon.png": (brand.OUT / "icon-256.png").read_bytes(),
        EXT / "media" / "icon.svg": (brand.BRANDING / "icon-mono.svg").read_bytes(),
        package_json: (
            json.dumps(
                brand.brand_package_json(json.loads(package_json.read_text("utf-8")), b),
                indent=2,
                ensure_ascii=False,
            )
            + "\n"
        ).encode("utf-8"),
    }
    originals = {path: path.read_bytes() for path in branded}
    try:
        for path, content in branded.items():
            path.write_bytes(content)
        subprocess.run(
            [node, "esbuild.mjs", "--production"], cwd=EXT, check=True, stdin=subprocess.DEVNULL
        )
        subprocess.run(
            [npx, "vsce", "package", "--target", t, "--skip-license", "--out", out],
            cwd=EXT,
            check=True,
            stdin=subprocess.DEVNULL,
        )
    finally:
        for path, content in originals.items():
            path.write_bytes(content)
    print(f"built {EXT / out} ({b['productName']}, publisher {b['publisher']})")


if __name__ == "__main__":
    main()
