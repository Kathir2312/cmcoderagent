# Phase 1 — Daily driver: status

**Status:** ✅ complete. **Snapshot:** tag `phase1` (the commit that adds this file).

Phase 1's goal ([PLAN.md](PLAN.md)): *make cmcoder good enough to use every day
on real projects*: long sessions that don't run out of context, sessions you
can resume and rewind, and a model that uses the right tools. Met.

## Delivered

All 12 items in [PLAN.md](PLAN.md):

1. **Auto-compaction:** at 80% of the window, older messages are summarised by
   the small model; the recent tail, the original request and the todo list
   are kept. `/compact` does it on demand.
2. **Permission prompt** stays on screen: the preview is clipped and the
   options always show (and a real dialog in the TUI).
3. **Real context window:** read from the gateway, probed, learned from
   "too long" errors and cached; `contextWindow` in settings wins.
4. **Right tools for file work:** prompt rules plus a redirect when Qwen uses
   Bash for reading/writing files. Measured: 100% tool choice.
5. **Managed settings** for administrators: an admin-only file that fails
   closed, locks providers and can forbid bypass mode.
6. **Sessions:** saved per project; `-c`, `--resume`, `/resume`, titles.
7. **Checkpoints and `/rewind`:** code and/or conversation, surviving restarts.
8. **TodoWrite:** a ☑ ► ☐ checklist, kept through compaction and resume.
9. **Small-model jobs:** titles and compaction summaries.
10. **Tool-call robustness:** tool calls written as text, a prompted
    tool-calling mode, argument repair, "closest match" hints for Edit.
11. **Textual TUI** (`--tui`), opt-in.
12. **20 eval tasks** with negative controls, run in CI on three OSes.

## Validation

| Where | Result |
|---|---|
| This repository (CI on Linux, macOS and Windows) | 418 tests passed (4 skipped); `ruff` and `pyright` clean; 20 mock evals |
| Your Windows machine, real gateway (Qwen3.6-27B, small model Qwen3.5-35B-A3B) | `pytest` passed; `doctor` all checks passed; **real-model evals 20/20** (after two check fixes); **tool choice 100%**; hands-on: `/rewind`, `-c`/`/resume`, titles, `--tui` dialog, todo checklist |

## Found in hands-on use, and fixed

| Found | Fix |
|---|---|
| Rule paths relative to `E:\`: the drive was taken as the project root | A drive root, the home folder and `~/.cmcoder` are never a project root; the banner and `doctor` show the root |
| Rewinding to message 1 left nothing for `-c` | The pre-rewind conversation is kept as a "Before rewind: …" session |
| Windows `\` in answers broke a check | Tools always show `/` |
| Qwen never called TodoWrite | Firmer prompt rule, plus a one-time reminder after 3 tool calls without a list |
| The in-progress mark ◐ showed as a box in cmd.exe | Now ► |
| `pip install … \| tail -5` failed but showed "exit 0" | The pipe's exit codes are reported (`exit 0 · pipe 1 0`) |
| Under `uv run`, commands used cmcoder's own Python | cmcoder's venv is removed from the commands' PATH |
| Successful commands showed only "exit 0" | The first 3 lines of output are shown (20 with `-v`) |

## Changed from the plan

- The Textual TUI stays **opt-in** (`--tui` or `"ui": "textual"`). The
  plain REPL has had all the hands-on use; the TUI only its dialog check.
- Added from hands-on use: the items in the table above.

## Carried into Phase 2

- Re-check on Windows: the pipe exit codes, the venv fix and the output
  preview (fixed after the last hands-on run).
- Confirm the real context window with the gateway administrator (now set
  by hand to 200000 in settings).
- Decide later whether the TUI becomes the default.

## Guides

- [python-guide.md](python-guide.md): the Phase 1 code for a day-1 Python developer.
- [langgraph-guide.md](langgraph-guide.md): the Phase 1 code mapped to LangChain/LangGraph.
