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

The check runs with bash (Git Bash on Windows) in the task's working copy and
passes on exit code 0.

Tool choice is scored too: each run counts the tools the model called and
how often it tried to do file work through Bash (cat > f << EOF, cat f,
grep -r, ...; see cmcoder/core/steer.py). The summary shows the share of
file work done with the file tools. CMCODER_EVAL_OUTPUT points at a file holding the agent's
final answer, and $PYTHON is the current Python interpreter (`python3` is not
available on most Windows machines).
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

from cmcoder.compat import SHELL_HELP, find_shell, to_shell_path
from cmcoder.core.steer import file_work_redirect

FILE_TOOLS = ("Read", "Write", "Edit", "Glob", "Grep")

ROOT = Path(__file__).resolve().parent
TASKS = ROOT / "tasks"


def run_task(
    task_dir: Path, run_dir: Path, args: argparse.Namespace, shell: str
) -> dict[str, object]:
    spec = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
    work = run_dir / task_dir.name
    shutil.copytree(task_dir / "repo", work)
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    # Keep task files byte-for-byte (and quiet "LF will be replaced by CRLF"
    # warnings on Windows), whatever the user's global core.autocrlf is.
    subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=work, check=True)
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
    env["PYTHON"] = to_shell_path(sys.executable)
    server = None
    if args.mock:
        from cmcoder.testing.mock_server import MockServer, MockState

        script = json.loads((task_dir / "mock_script.json").read_text(encoding="utf-8"))
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
        "stream-json",
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
            encoding="utf-8",
            errors="replace",
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
    tools: dict[str, int] = {}
    bash_file_work = 0
    for line in stdout.splitlines():
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        if data.get("type") == "result":
            result = data
        elif data.get("type") == "tool_use":
            name = str(data.get("name"))
            tools[name] = tools.get(name, 0) + 1
            command = (data.get("input") or {}).get("command")
            if name == "Bash" and isinstance(command, str) and file_work_redirect(command):
                bash_file_work += 1
    output_file = work / ".cmcoder_eval_output.txt"
    output_file.write_text(str(result.get("result", "")), encoding="utf-8")
    (work / ".cmcoder_eval_stderr.txt").write_text(stderr, encoding="utf-8")

    check = subprocess.run(
        [shell, "-c", spec["check"]],
        cwd=work,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        env={**env, "CMCODER_EVAL_OUTPUT": to_shell_path(output_file)},
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
        "tools": tools,
        "file_tool_calls": sum(tools.get(t, 0) for t in FILE_TOOLS),
        "bash_file_work": bash_file_work,
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

    shell = find_shell()
    if shell is None:
        print(SHELL_HELP, file=sys.stderr)
        return 2
    tasks = sorted(p for p in TASKS.iterdir() if (p / "task.json").exists())
    if args.task:
        tasks = [t for t in tasks if t.name in args.task]
    run_dir = ROOT / ".runs" / time.strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True)

    results = []
    for task in tasks:
        r = run_task(task, run_dir, args, shell)
        results.append(r)
        mark = "PASS" if r["passed"] else "FAIL"
        used = " ".join(f"{k}:{v}" for k, v in sorted(dict(r["tools"]).items()))  # type: ignore[call-overload]
        print(
            f"{mark}  {r['task']:<24} {r['agent_status']!s:<12} turns={r['turns']} "
            f"tokens={r['prompt_tokens']}/{r['completion_tokens']} {r['seconds']}s "
            f"tools=[{used}] bash-file-work={r['bash_file_work']}",
            flush=True,
        )
        if not r["passed"]:
            print(f"      check: {str(r['check_output']).strip()[-300:]}")
            print(f"      workdir: {r['workdir']}")
    (run_dir / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    passed = sum(1 for r in results if r["passed"])
    file_tools = sum(int(r["file_tool_calls"]) for r in results)  # type: ignore[call-overload]
    via_bash = sum(int(r["bash_file_work"]) for r in results)  # type: ignore[call-overload]
    share = f"{100 * file_tools // max(1, file_tools + via_bash)}%"
    print(f"\n{passed}/{len(results)} passed · results in {run_dir / 'results.json'}")
    print(
        f"Tool choice: {file_tools} file-tool calls, {via_bash} attempts at file work through "
        f"Bash ({share} of file work done with the file tools)"
    )
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
