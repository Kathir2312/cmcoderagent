"""The release bundle (Phase 6 item 8): every file for developers, in one
folder with one version, assembled from the release build's artifacts only
when all of them are there (each build job passed its gates first).

    python packaging/bundle.py --artifacts DIR --out DIR

DIR holds the downloaded artifacts, one folder per artifact (as
actions/download-artifact writes them). Output: the bundle folder with
README-FIRST.txt, GATE-REPORT.md (from the gate-<target> artifacts: what each
platform's build job ran and passed), SHA256SUMS.txt and the files by IDE.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGETS = ("win32-x64", "darwin-arm64", "linux-x64")
TARGET_NAMES = {
    "win32-x64": "Windows",
    "darwin-arm64": "macOS (Apple silicon)",
    "linux-x64": "Linux",
}


@dataclass(frozen=True)
class Item:
    folder: str  # in the bundle
    artifact: str  # the artifact's name (its folder in --artifacts)
    pattern: str  # the file in it
    name: str  # its name in the bundle


def items() -> list[Item]:
    out: list[Item] = []
    for t in TARGETS:
        out.append(
            Item(
                "terminal", f"cmcoder-terminal-{t}", f"cmcoder-{t}.zip", f"cmcoder-terminal-{t}.zip"
            )
        )
        out.append(Item("vscode", f"cmcoder-{t}.vsix", f"cmcoder-{t}.vsix", f"cmcoder-{t}.vsix"))
        out.append(
            Item(
                "netbeans",
                f"cmcoder-netbeans-{t}.nbm",
                f"cmcoder-netbeans-{t}.nbm",
                f"cmcoder-netbeans-{t}.nbm",
            )
        )
    out.append(
        Item(
            "visualstudio",
            "cmcoder-visualstudio.vsix",
            "cmcoder-visualstudio.vsix",
            "cmcoder-visualstudio-win32-x64.vsix",
        )
    )
    out.append(Item("eclipse", "cmcoder-eclipse", "cmcoder-eclipse-*.zip", "cmcoder-eclipse.zip"))
    return out


def version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]


def find(artifacts: Path, item: Item) -> Path | None:
    found = (
        sorted((artifacts / item.artifact).glob(item.pattern))
        if (artifacts / item.artifact).is_dir()
        else []
    )
    return found[0] if len(found) == 1 else None


def gate_report(artifacts: Path, ver: str) -> str:
    """One row per check, one column per platform: ✅ where that platform's job ran it and passed."""
    ran: dict[str, set[str]] = {}
    order: list[str] = []
    for t in TARGETS:
        f = artifacts / f"gate-{t}" / f"gate-{t}.txt"
        if not f.is_file():
            raise SystemExit(f"no gate results for {t} ({f})")
        for line in f.read_text(encoding="utf-8").splitlines():
            check = line.strip()
            if not check:
                continue
            if check not in ran:
                ran[check] = set()
                order.append(check)
            ran[check].add(t)
    head = "| Check | " + " | ".join(TARGET_NAMES[t] for t in TARGETS) + " |"
    rows = [head, "|---" * (len(TARGETS) + 1) + "|"]
    for check in order:
        rows.append(
            f"| {check} | " + " | ".join("✅" if t in ran[check] else "—" for t in TARGETS) + " |"
        )
    return (
        f"# Gate report, cmcoder {ver}\n\n"
        "Every check ran in the release build, on the platform's own runner, "
        "with the files in this bundle (or the same build of them) and no Python "
        "on the search path. ✅ passed; — not run on that platform (the IDE "
        "doesn't exist there, or the check runs on one platform only). A failed "
        "check stops the build: no bundle is made.\n\n" + "\n".join(rows) + "\n\n"
        "## Not covered by automated tests\n\n"
        "- Signing: the files aren't code-signed until the company has a certificate "
        "(Windows SmartScreen, antivirus and macOS Gatekeeper may warn).\n"
        "- A real gateway and model: the gates use a scripted model server; "
        "`cmcoder doctor` checks a real one on the developer's PC.\n"
        "- Visual Studio 2022 itself: GitHub's runner has a newer Visual Studio; the "
        "extension is compiled against the 17.10 SDK.\n"
        "- Older IDE versions on Windows and macOS: Eclipse's oldest supported "
        "version and NetBeans 28 run on Linux and macOS (Eclipse) or Linux (NetBeans).\n"
        "- The code search set-up dialogs in the IDEs (the steps behind them are "
        "tested through cmcoder).\n"
    )


README = """cmcoder {version}: files for developers
=========================================

Pick the file for your IDE and platform. Nothing else is needed on the PC:
no Python, no Node.js. On Windows, shell commands need Git for Windows.

  Terminal       terminal/cmcoder-terminal-<platform>.zip
                 Unzip, run install.cmd (Windows) or sh install.sh, open a new
                 terminal, run: cmcoder doctor

  VS Code        vscode/cmcoder-<platform>.vsix
                 Extensions → ⋯ → Install from VSIX…

  Visual Studio  visualstudio/cmcoder-visualstudio-win32-x64.vsix
  2022           Close Visual Studio, double-click the file, Install.

  Eclipse        eclipse/cmcoder-eclipse.zip   (all platforms in one)
  2024-06+       Help → Install New Software… → Add… → Archive…, the zip.

  NetBeans 28+   netbeans/cmcoder-netbeans-<platform>.nbm
                 Tools → Plugins → Downloaded → Add Plugins…, the file.

  <platform>: win32-x64 (Windows), darwin-arm64 (Mac, Apple silicon),
  linux-x64 (Linux).

First time, once: your company's gateway and your API key. See the guides
(docs/guides/ in the repository): setup.md, then the one for your IDE;
troubleshooting.md if something is wrong. Every IDE has "Copy Diagnostics"
for a problem report (it never contains your key).

GATE-REPORT.md lists what was tested automatically, and what wasn't.
SHA256SUMS.txt has the files' checksums.
"""


def assemble(artifacts: Path, out: Path) -> list[Path]:
    ver = version()
    missing: list[str] = []
    chosen: list[tuple[Item, Path]] = []
    for item in items():
        f = find(artifacts, item)
        if f is None:
            missing.append(f"{item.artifact}/{item.pattern}")
        else:
            chosen.append((item, f))
    if missing:
        raise SystemExit(
            "incomplete: no bundle without every file. Missing:\n  " + "\n  ".join(missing)
        )
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    written: list[Path] = []
    for item, f in chosen:
        dest = out / item.folder / item.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(f, dest)
        written.append(dest)
    (out / "README-FIRST.txt").write_text(README.format(version=ver), encoding="utf-8")
    (out / "GATE-REPORT.md").write_text(gate_report(artifacts, ver), encoding="utf-8")
    sums = []
    for f in sorted(written + [out / "README-FIRST.txt", out / "GATE-REPORT.md"]):
        digest = hashlib.sha256(f.read_bytes()).hexdigest()
        sums.append(f"{digest}  {f.relative_to(out).as_posix()}")
    (out / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="utf-8")
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--artifacts", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    files = assemble(args.artifacts, args.out)
    print(f"bundle: {args.out} ({len(files)} files, cmcoder {version()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
