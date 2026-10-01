"""Eval runner: runs cmcoder on small task repos and checks the result.

  # Against your real endpoint (uses your settings / CMCODER_* env):
  uv run python evals/run.py
  uv run python evals/run.py --model corp:qwen3-7b --task fix-off-by-one

  # Harness-only check with scripted replies (CI; no model needed):
  uv run python evals/run.py --mock

Each task folder holds:
  task.json         {"prompt": ..., "check": "<shell command>", "permission_mode": ..., "max_turns": ...}
  repo/             the starting files (copied to a fresh git repo per run)
  mock_script.json  scripted model replies used with --mock

The check runs in the task's working copy and passes on exit code 0.
CMCODER_EVAL_OUTPUT points at a file holding the agent's final answer.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TASKS = ROOT / "tasks"


def run_task(task_dir: Path, run_dir: Path, args: argparse.Namespace) -> dict[str, object]:
    spec = json.loads((task_dir / "task.json").read_text())
    work = run_dir / task_dir.name
    shutil.copytree(task_dir / "repo", work)
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    subprocess.run(["git", "add", "-A"], cwd=work, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=eval",
            "-c",
            "user.email=eval@example.invalid",
            "commit",
            "-qm",
            "start",
        ],
        cwd=work,
        check=True,
    )

    env = dict(os.environ)
    server = None
    if args.mock:
        from cmcoder.testing.mock_server import MockServer, MockState

        script = json.loads((task_dir / "mock_script.json").read_text())
        server = MockServer(MockState(script, api_key="sk-eval"))
        server.__enter__()
        env.update(
            {
                "CMCODER_BASE_URL": server.base_url,
                "CMCODER_API_KEY": "sk-eval",
                "CMCODER_MODEL": "qwen3-27b",
                "CMCODER_CONFIG_DIR": str(run_dir / ".config"),
            }
        )
        for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
            env.pop(var, None)

    cmd = [
        sys.executable,
        "-m",
        "cmcoder",
        "-p",
        spec["prompt"],
        "--output-format",
        "json",
        "--permission-mode",
        spec.get("permission_mode", "bypassPermissions"),
        "--max-turns",
        str(spec.get("max_turns", 30)),
    ]
    if args.model:
        cmd += ["--model", args.model]
    started = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=work,
            env=env,
            capture_output=True,
            text=True,
            timeout=args.timeout,
            stdin=subprocess.DEVNULL,
        )
        stdout, stderr, timed_out = proc.stdout, proc.stderr, False
    except subprocess.TimeoutExpired as e:
        stdout = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        stderr, timed_out = "timed out", True
    finally:
        if server is not None:
            server.__exit__(None, None, None)
    duration = time.monotonic() - started

    result: dict[str, object] = {}
    for line in stdout.splitlines():
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("type") == "result":
            result = data
    output_file = work / ".cmcoder_eval_output.txt"
    output_file.write_text(str(result.get("result", "")))
    (work / ".cmcoder_eval_stderr.txt").write_text(stderr)

    check = subprocess.run(
        spec["check"],
        shell=True,
        cwd=work,
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "CMCODER_EVAL_OUTPUT": str(output_file)},
    )
    usage = result.get("usage") or {}
    assert isinstance(usage, dict)
    return {
        "task": task_dir.name,
        "passed": check.returncode == 0 and not timed_out,
        "agent_status": "timeout" if timed_out else result.get("subtype", "no result"),
        "turns": result.get("num_turns"),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "seconds": round(duration, 1),
        "check_output": (check.stdout + check.stderr)[-500:],
        "workdir": str(work),
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--mock", action="store_true", help="use scripted replies instead of a real model"
    )
    ap.add_argument("--model", help="model to evaluate (default: settings)")
    ap.add_argument("--task", action="append", help="run only these tasks")
    ap.add_argument("--timeout", type=int, default=900, help="seconds per task")
    args = ap.parse_args()

    tasks = sorted(p for p in TASKS.iterdir() if (p / "task.json").exists())
    if args.task:
        tasks = [t for t in tasks if t.name in args.task]
    run_dir = ROOT / ".runs" / time.strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True)

    results = []
    for task in tasks:
        r = run_task(task, run_dir, args)
        results.append(r)
        mark = "PASS" if r["passed"] else "FAIL"
        print(
            f"{mark}  {r['task']:<24} {r['agent_status']!s:<12} turns={r['turns']} "
            f"tokens={r['prompt_tokens']}/{r['completion_tokens']} {r['seconds']}s",
            flush=True,
        )
        if not r["passed"]:
            print(f"      check: {str(r['check_output']).strip()[-300:]}")
            print(f"      workdir: {r['workdir']}")
    (run_dir / "results.json").write_text(json.dumps(results, indent=2))
    passed = sum(1 for r in results if r["passed"])
    print(f"\n{passed}/{len(results)} passed · results in {run_dir / 'results.json'}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
