"""The terminal package: the standalone program with install scripts, for
developers who want `cmcoder` in a terminal without Python (Phase 6).

    uv run python packaging/terminal.py      # after packaging/build.py

Output: dist/terminal/cmcoder-<target>.zip (e.g. win32-x64), holding
cmcoder-<target>/ with the program folder, the install and uninstall scripts
for that platform, and a README.txt. The scripts install for the user only
(no administrator or root rights) and add the program to the user's PATH.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent / "terminal"
WINDOWS_SCRIPTS = ("install.cmd", "install.ps1", "uninstall.cmd", "uninstall.ps1")
POSIX_SCRIPTS = ("install.sh", "uninstall.sh")

sys.path.insert(0, str(ROOT / "packaging"))

from vsix import target  # noqa: E402


def version() -> str:
    import tomllib

    with open(ROOT / "pyproject.toml", "rb") as f:
        return str(tomllib.load(f)["project"]["version"])


def product() -> str:
    data = json.loads((ROOT / "branding" / "brand.json").read_text(encoding="utf-8"))
    return str(data.get("productName") or "cmcoder")


def _add(zf: zipfile.ZipFile, path: Path, name: str, executable: bool = False) -> None:
    info = zipfile.ZipInfo.from_file(path, name)  # follows links: a zip can't hold them
    mode = path.stat().st_mode
    if executable:
        mode |= stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    info.external_attr = (stat.S_IFREG | stat.S_IMODE(mode)) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    zf.writestr(info, path.read_bytes())


def build(program: Path, out_dir: Path, platform: str, windows: bool) -> Path:
    """Zip `program` (the standalone folder) with the scripts for `platform`."""
    exe = program / ("cmcoder.exe" if windows else "cmcoder")
    if not exe.is_file():
        raise SystemExit(f"{exe} is missing: run packaging/build.py first")
    out_dir.mkdir(parents=True, exist_ok=True)
    top = f"cmcoder-{platform}"
    out = out_dir / f"{top}.zip"
    readme = (
        (SCRIPTS / "README.txt")
        .read_text(encoding="utf-8")
        .format(product=product(), version=version(), platform=platform, command="cmcoder")
    )
    if windows:
        readme = readme.replace("\n", "\r\n")
    with zipfile.ZipFile(out, "w") as zf:
        for root, _dirs, files in os.walk(program, followlinks=True):
            for file in sorted(files):
                path = Path(root) / file
                rel = path.relative_to(program).as_posix()
                _add(zf, path, f"{top}/cmcoder/{rel}", executable=path == exe)
        for script in WINDOWS_SCRIPTS if windows else POSIX_SCRIPTS:
            _add(zf, SCRIPTS / script, f"{top}/{script}", executable=not windows)
        info = zipfile.ZipInfo(f"{top}/README.txt")
        info.external_attr = (stat.S_IFREG | 0o644) << 16
        zf.writestr(info, readme)
    return out


def main() -> None:
    out = build(
        ROOT / "dist" / "cmcoder",
        ROOT / "dist" / "terminal",
        target(),
        windows=sys.platform == "win32",
    )
    print(f"built {out}")


if __name__ == "__main__":
    main()
