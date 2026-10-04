"""Eval runner: runs cmcoder on small task repos and checks the result.

  # Against your real endpoint (uses your settings / CMCODER_* env):
  uv run python evals/run.py
  uv run python evals/run.py --model corp:qwen3-7b --task fix-off-by-one

  # Harness-only check with scripted replies (CI; no model needed):
  uv run python evals/run.py --mock

  # Through the VS Code extension's protocol (cmcoder --protocol stdio), or
  # both ways, checking that each task gives the same result and tool calls:
  uv run python evals/run.py --mock --via both

  # With scripted replies served the way Open WebUI serves them (an Ollama
  # model, or a model on an OpenAI-compatible backend):
  uv run python evals/run.py --mock --gateway openwebui-ollama

  # Code search (Phase 5): tasks with "index": true get the project indexed
  # first (your rag.embeddingModel; the mock's with --mock). Compare a run
  # with code search and one without:
  uv run python evals/run.py --rag on      # the default
  uv run python evals/run.py --rag off

Each task folder holds:
  task.json         {"prompt": ..., "check": "<shell command>", "permission_mode": ...,
                     "max_turns": ..., "args": [extra cmcoder arguments, e.g. --mcp-config]}
  repo/             the starting files (copied to a fresh git repo per run)
  ("index": true in task.json: the copy is indexed for code search before the run)
  mock_script.json  scripted model replies used with --mock

The check runs with bash (Git Bash on Windows) in the task's working copy and
passes on exit code 0.

Tool choice is scored too: each run counts the tools the model called and
how often it tried to do file work through Bash (cat > f << EOF, cat f,
grep -r, ...; see cmcoder/core/steer.py). The summary shows the share of
file work done with the file tools. CMCODER_EVAL_OUTPUT points at a file holding the agent's
final answer, and $PYTHON is the current Python interpreter (`python3` is not
available on most Windows machines); CMCODER_EVAL_PYTHON is the same
interpreter as a native path, for commands cmcoder starts itself (MCP servers).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from cmcoder.compat import SHELL_HELP, find_shell, to_shell_path
from cmcoder.core.steer import file_work_redirect

FILE_TOOLS = ("Read", "Write", "Edit", "Glob", "Grep")

ROOT = Path(__file__).resolve().parent

# --mock --gateway: how the scripted replies are served (testing/mock_server.py).
# Name -> (Open WebUI models and their backends, or None for LiteLLM; model).
GATEWAYS: dict[str, tuple[dict[str, str] | None, str]] = {
    "litellm": (None, "qwen3-27b"),
    "openwebui-ollama": ({"qwen3:32b": "ollama"}, "qwen3:32b"),
    "openwebui-openai": ({"qwen3-vllm": "openai"}, "qwen3-vllm"),
}
TASKS = ROOT / "tasks"


def run_cli(
    cmd: list[str], prompt: str, cwd: Path, env: dict[str, str], timeout: float
) -> tuple[str, str, bool]:
    """Runs `cmcoder -p` for one prompt: (stream-json output, stderr, timed out)."""
    try:
        proc = subprocess.run(
            [*cmd, "-p", prompt, "--output-format", "stream-json"],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as e:
        stdout = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        return stdout, "timed out", True
    return proc.stdout, proc.stderr, False


def run_stdio(
    cmd: list[str], prompt: str, cwd: Path, env: dict[str, str], timeout: float
) -> tuple[str, str, bool]:
    """Drives `cmcoder --protocol stdio` like the VS Code extension does, for one
    prompt. Permission requests are denied, as `-p` does. Returns (the events
    as stream-json lines, stderr, timed out)."""
    proc = subprocess.Popen(
        [*cmd, "--protocol", "stdio"],
        cwd=cwd,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.stdin and proc.stdout and proc.stderr
    stderr: list[str] = []
    reader = threading.Thread(target=lambda: stderr.extend(proc.stderr), daemon=True)  # type: ignore[arg-type]
    reader.start()
    timer = threading.Timer(timeout, proc.kill)
    timer.start()

    def send(message: dict[str, object]) -> None:
        proc.stdin.write(json.dumps(message) + "\n")  # type: ignore[union-attr]
        proc.stdin.flush()  # type: ignore[union-attr]

    lines: list[str] = []
    try:
        send({"type": "user_message", "text": prompt})
        for line in proc.stdout:
            lines.append(line)
            event = json.loads(line)
            if event.get("type") == "permission_request":
                send(
                    {
                        "type": "permission_response",
                        "request_id": event["request_id"],
                        "allow": False,
                    }
                )
            elif event.get("type") == "result":
                break
        send({"type": "shutdown"})
        proc.stdin.close()
        proc.wait(timeout=30)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        proc.kill()
    finally:
        timed_out = not timer.is_alive() and proc.returncode not in (0, None)
        timer.cancel()
        reader.join(timeout=5)
    return "".join(lines), "".join(stderr), timed_out


def run_task(
    task_dir: Path, run_dir: Path, args: argparse.Namespace, shell: str, via: str = "cli"
) -> dict[str, object]:
    spec = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
    work = run_dir / via / task_dir.name
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
    env["CMCODER_EVAL_PYTHON"] = sys.executable  # native path, e.g. for an MCP server command
    server = None
    if args.mock:
        from cmcoder.testing.mock_server import MockServer, MockState

        script = json.loads((task_dir / "mock_script.json").read_text(encoding="utf-8"))
        webui, model = GATEWAYS[args.gateway]
        server = MockServer(MockState(script, api_key="sk-eval", openwebui=webui))
        server.__enter__()
        env.update(
            {
                "CMCODER_BASE_URL": server.root_url if webui is not None else server.base_url,
                "CMCODER_API_KEY": "sk-eval",
                "CMCODER_MODEL": model,
                "CMCODER_CONFIG_DIR": str(run_dir / ".config"),
            }
        )
        if webui is not None:
            env["CMCODER_PROVIDER_TYPE"] = "openwebui"
        env["CMCODER_EMBEDDING_MODEL"] = "nomic-embed-text" if webui else "text-embedding-3-small"
        for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
            env.pop(var, None)

    cmd = [
        sys.executable,
        "-m",
        "cmcoder",
        "--permission-mode",
        spec.get("permission_mode", "bypassPermissions"),
        "--max-turns",
        str(spec.get("max_turns", 30)),
    ]
    cmd += [str(a) for a in spec.get("args", [])]  # e.g. --mcp-config
    if args.model:
        cmd += ["--model", args.model]
    indexed = False
    if args.rag == "off":
        env["CMCODER_RAG"] = "off"
    elif spec.get("index"):
        # Code search: index the working copy first (a failure leaves the
        # task to run without it, as a project without an index would).
        built = subprocess.run(
            [sys.executable, "-m", "cmcoder", "index"],
            cwd=work,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            stdin=subprocess.DEVNULL,
        )
        indexed = built.returncode == 0
        if not indexed:
            print(f"      (no code index: {(built.stderr or built.stdout).strip()[-200:]})")
    started = time.monotonic()
    run = run_stdio if via == "stdio" else run_cli
    try:
        stdout, stderr, timed_out = run(cmd, spec["prompt"], work, env, float(args.timeout))
    finally:
        if indexed:  # its index lives in the config folder: don't leave it behind
            subprocess.run(
                [sys.executable, "-m", "cmcoder", "index", "--clear"],
                cwd=work,
                env=env,
                capture_output=True,
                timeout=120,
                stdin=subprocess.DEVNULL,
            )
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
    truncated = list(server.state.truncated) if server is not None else []
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
        # Ollama drops the start of a prompt longer than num_ctx without an error.
        "passed": check.returncode == 0 and not timed_out and not truncated,
        "agent_status": "timeout" if timed_out else result.get("subtype", "no result"),
        "turns": result.get("num_turns"),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "seconds": round(duration, 1),
        "tools": tools,
        "file_tool_calls": sum(tools.get(t, 0) for t in FILE_TOOLS),
        "bash_file_work": bash_file_work,
        "check_output": (check.stdout + check.stderr)[-500:]
        + (f"\nOllama truncated the prompt ({len(truncated)} requests)" if truncated else ""),
        "workdir": str(work),
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--mock", action="store_true", help="use scripted replies instead of a real model"
    )
    ap.add_argument(
        "--gateway",
        choices=sorted(GATEWAYS),
        default="litellm",
        help="with --mock: serve the replies like LiteLLM (default) or Open WebUI",
    )
    ap.add_argument(
        "--rag",
        choices=["on", "off"],
        default="on",
        help='code search for tasks marked "index": true (off: compare without it)',
    )
    ap.add_argument("--model", help="model to evaluate (default: settings)")
    ap.add_argument("--task", action="append", help="run only these tasks")
    ap.add_argument("--timeout", type=int, default=900, help="seconds per task")
    ap.add_argument(
        "--via",
        choices=["cli", "stdio", "both"],
        default="cli",
        help="drive cmcoder with -p (cli), through the VS Code protocol (stdio), or both "
        "and check they agree",
    )
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

    vias = ["cli", "stdio"] if args.via == "both" else [args.via]
    results = []
    mismatches: list[str] = []
    for task in tasks:
        by_via = {}
        for via in vias:
            r = run_task(task, run_dir, args, shell, via)
            r["via"] = via
            by_via[via] = r
            results.append(r)
            mark = "PASS" if r["passed"] else "FAIL"
            used = " ".join(f"{k}:{v}" for k, v in sorted(dict(r["tools"]).items()))  # type: ignore[call-overload]
            how = f" [{via}]" if len(vias) > 1 else ""
            print(
                f"{mark}  {r['task']:<24}{how} {r['agent_status']!s:<12} turns={r['turns']} "
                f"tokens={r['prompt_tokens']}/{r['completion_tokens']} {r['seconds']}s "
                f"tools=[{used}] bash-file-work={r['bash_file_work']}",
                flush=True,
            )
            if not r["passed"]:
                print(f"      check: {str(r['check_output']).strip()[-300:]}")
                print(f"      workdir: {r['workdir']}")
        if len(by_via) == 2:
            cli, stdio = by_via["cli"], by_via["stdio"]
            keys = ("passed", "agent_status", "turns", "tools")
            if any(cli[k] != stdio[k] for k in keys):
                diff = ", ".join(f"{k}: {cli[k]} vs {stdio[k]}" for k in keys if cli[k] != stdio[k])
                mismatches.append(f"{task.name} ({diff})")
                print(f"      PARITY MISMATCH cli vs stdio: {diff}")
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
    if len(vias) == 2:
        print(
            f"Parity (-p vs --protocol stdio): {len(tasks) - len(mismatches)}/{len(tasks)} tasks "
            "gave the same result and tool calls"
            + (f"; differ: {'; '.join(mismatches)}" if mismatches else "")
        )
    return 0 if passed == len(results) and not mismatches else 1


if __name__ == "__main__":
    sys.exit(main())
