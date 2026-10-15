"""docs/learn/phase8/labs/: each lab runs as the guide says and shows what
the guide describes, so the labs keep working as the engine changes."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

LABS = Path(__file__).resolve().parent.parent / "docs" / "learn" / "phase8" / "labs"

# Per lab: lines its output must contain.
EXPECTED = {
    "m1_model.py": [
        "CmcoderChatOpenAI  answer='Blue.'  reasoning='Rayleigh scattering.'",
        "CmcoderChatOpenAI  streamed to the user: 'Blue.'",
        "CmcoderChatOpenAI  reply='pong'",
        "reply: from main",
    ],
    "m2_tools.py": [
        "status=success",
        "Invalid arguments for Read: file_path: Field requi",
        "project A",
        "project B",
        "mcp__demo__shout: status=success content='HELLO'",
        "mcp__demo__fail: status=error",
    ],
    "m3_graph.py": [
        "model  -> tool calls ['Read(app.py)']",
        "files_read: ['app.py', 'test_app.py']",
        "the first turn's are still there",
        "recursion_limit=6: GraphRecursionError",
        "max_steps=3: stopped cleanly after 3 model calls",
    ],
}


@pytest.mark.parametrize("lab", sorted(EXPECTED))
def test_lab_runs(lab: str) -> None:
    out = subprocess.run(
        [sys.executable, str(LABS / lab)], capture_output=True, text=True, timeout=180
    )
    assert out.returncode == 0, out.stderr[-2000:]
    for line in EXPECTED[lab]:
        assert line in out.stdout, f"{lab}: no {line!r} in:\n{out.stdout[-3000:]}"
