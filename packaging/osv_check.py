"""Known vulnerabilities in what the IDE plugins ship (Phase 6 security
review): OpenJFX inside the NetBeans plugin, and the NuGet packages inside the
Visual Studio extension, looked up in OSV (osv.dev). npm and Python packages
have their own audits (npm audit, pip-audit).

    python packaging/osv_check.py        # exit 1 if any is known vulnerable
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JAVAFX = (
    "javafx-base",
    "javafx-graphics",
    "javafx-controls",
    "javafx-media",
    "javafx-web",
    "javafx-swing",
)


def packages() -> list[tuple[str, str, str]]:
    """(ecosystem, name, version) of everything the plugins put on developers' PCs."""
    pom = (ROOT / "netbeans/pom.xml").read_text(encoding="utf-8")
    m = re.search(r"<javafx.version>([^<]+)</javafx.version>", pom)
    if not m:
        raise SystemExit("no javafx.version in netbeans/pom.xml")
    out = [("Maven", f"org.openjfx:{a}", m.group(1)) for a in JAVAFX]
    for csproj in sorted((ROOT / "visualstudio/src").glob("*/*.csproj")):
        text = csproj.read_text(encoding="utf-8")
        for name, version in re.findall(
            r'<PackageReference Include="([^"]+)" Version="([^"]+)"', text
        ):
            if ("NuGet", name, version) not in out:
                out.append(("NuGet", name, version))
    return out


def main() -> int:
    pkgs = packages()
    body = {"queries": [{"package": {"ecosystem": e, "name": n}, "version": v} for e, n, v in pkgs]}
    req = urllib.request.Request(  # noqa: S310  # nosec B310 - a fixed https address
        "https://api.osv.dev/v1/querybatch",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310  # nosec B310
        results = json.load(resp)["results"]
    bad = 0
    for (e, n, v), r in zip(pkgs, results, strict=True):
        ids = [x["id"] for x in r.get("vulns", [])]
        print(f"{e} {n} {v}: {', '.join(ids) if ids else 'no known vulnerabilities'}")
        bad += len(ids)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
