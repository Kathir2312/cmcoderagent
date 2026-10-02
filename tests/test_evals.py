"""The eval tasks themselves: well-formed, and their checks measure real work."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from cmcoder.compat import find_shell, to_shell_path

TASKS = sorted(
    p for p in (Path(__file__).parent.parent / "evals" / "tasks").iterdir() if p.is_dir()
)


def test_there_are_twenty_tasks() -> None:
    assert len(TASKS) >= 20


@pytest.mark.parametrize("task", TASKS, ids=lambda p: p.name)
def test_task_is_well_formed(task: Path) -> None:
    spec = json.loads((task / "task.json").read_text(encoding="utf-8"))
    assert spec["prompt"] and spec["check"]
    assert spec.get("permission_mode", "bypassPermissions") in (
        "default",
        "acceptEdits",
        "plan",
        "bypassPermissions",
    )
    script = json.loads((task / "mock_script.json").read_text(encoding="utf-8"))
    assert isinstance(script, list) and "content" in script[-1]  # ends with an answer
    assert (task / "repo").is_dir()


@pytest.mark.parametrize("task", TASKS, ids=lambda p: p.name)
def test_check_fails_before_the_work_is_done(task: Path, tmp_path: Path) -> None:
    """A check that passes on the untouched repo would measure nothing."""
    shell = find_shell()
    if shell is None:
        pytest.skip("no bash")
    spec = json.loads((task / "task.json").read_text(encoding="utf-8"))
    work = tmp_path / "work"
    shutil.copytree(task / "repo", work)
    output = work / ".cmcoder_eval_output.txt"
    output.write_text("", encoding="utf-8")
    env = {
        **os.environ,
        "PYTHON": to_shell_path(sys.executable),
        "CMCODER_EVAL_OUTPUT": to_shell_path(output),
    }
    r = subprocess.run(
        [shell, "-c", spec["check"]], cwd=work, env=env, capture_output=True, timeout=60
    )
    assert r.returncode != 0, f"{task.name}: the check passes without any work"
