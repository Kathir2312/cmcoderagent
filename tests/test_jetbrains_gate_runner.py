"""jetbrains/gate/run-gate.sh with a stand-in for the IDE: an IDE that gets
stuck starting up (seen on macOS runners: IntelliJ waits for its licensing
service and never gets past its configuration store) is stopped and started
once more with a fresh profile; a second stuck start fails the gate."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "jetbrains" / "gate" / "run-gate.sh"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="the restart is for macOS and Linux (on Windows the launcher may hand over)",
)

# Stands in for the IDE's launcher: the first $STUCK_STARTS starts hang like
# the stuck IntelliJ; later ones write the self-test's first step and its result.
LAUNCHER = r"""#!/usr/bin/env bash
props=$IDEA_PROPERTIES
result=$(sed -n 's/^cmcoder.gate=//p' "$props")
config=$(sed -n 's/^idea.config.path=//p' "$props")
log=$(sed -n 's/^idea.log.path=//p' "$props")
echo "$config" >> "$(dirname "$result")/launches"
mkdir -p "$log"
n=$(wc -l < "$(dirname "$result")/launches")
if [ "$n" -le "$STUCK_STARTS" ]; then
  echo "awaiting LicensingFacade.getInstance()" > "$log/idea.log"
  exec sleep 600
fi
echo "00:00:01 loaded in the stand-in IDE" > "$result.steps"
echo '{"ok":true,"failure":null,"steps":["loaded"]}' > "$result"
"""


def fake_ide(tmp_path: Path) -> Path:
    ide = tmp_path / "ide"
    (ide / "bin").mkdir(parents=True)
    launcher = ide / "bin" / "idea.sh"
    launcher.write_text(LAUNCHER, encoding="utf-8")
    launcher.chmod(0o755)
    launch = [{"os": os_name, "launcherPath": "bin/idea.sh"} for os_name in ("Linux", "macOS")]
    info = {"name": "IntelliJ IDEA", "version": "0.0", "buildNumber": "0.0.1", "launch": launch}
    (ide / "product-info.json").write_text(json.dumps(info), encoding="utf-8")
    return ide


def gate_zip(tmp_path: Path) -> Path:
    path = tmp_path / "cmcoder-jetbrains-0.0-gate.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("cmcoder-jetbrains/lib/cmcoder-jetbrains.jar", "jar")
        z.writestr("cmcoder-jetbrains/panel/test-driver.js", "// driver")
    return path


def run_gate(
    tmp_path: Path, stuck_starts: int, timeout: int
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    env = {**os.environ, "STUCK_STARTS": str(stuck_starts), "TMPDIR": str(tmp_path)}
    out = subprocess.run(
        [
            "bash", str(RUNNER),
            "--zip", str(gate_zip(tmp_path)),
            "--ide", str(fake_ide(tmp_path)),
            "--python", sys.executable,
            "--timeout", str(timeout),
            "--startup-timeout", "3",
        ],
        capture_output=True, text=True, env=env, timeout=180,
    )  # fmt: skip
    launches = [
        line for f in tmp_path.glob("cmcoder-ij-gate-*/launches") for line in f.read_text().split()
    ]
    return out, launches


def test_a_healthy_start_is_not_restarted(tmp_path: Path) -> None:
    out, launches = run_gate(tmp_path, stuck_starts=0, timeout=30)
    assert out.returncode == 0, out.stdout + out.stderr
    assert len(launches) == 1
    assert "stuck starting up" not in out.stdout
    assert "The IntelliJ gate passed (IntelliJ IDEA 0.0 0.0.1 on" in out.stdout


def test_a_stuck_start_is_restarted_once_with_a_fresh_profile(tmp_path: Path) -> None:
    out, launches = run_gate(tmp_path, stuck_starts=1, timeout=30)
    assert out.returncode == 0, out.stdout + out.stderr
    assert len(launches) == 2 and launches[0] != launches[1]  # a fresh profile
    assert "hasn't started the self-test after 3 s" in out.stdout
    assert "log: awaiting LicensingFacade" in out.stdout  # why, from the first IDE's log
    assert "on the second start: the first was stuck starting up" in out.stdout


def test_a_second_stuck_start_fails_the_gate(tmp_path: Path) -> None:
    out, launches = run_gate(tmp_path, stuck_starts=2, timeout=8)
    assert out.returncode == 1
    assert len(launches) == 2  # one restart, not more
    assert "No gate result after 8 s" in out.stderr
