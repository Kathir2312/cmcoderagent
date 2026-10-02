# Phase 0 — Foundations: status

**Status:** ✅ complete. **Snapshot:** commit `ba6669f` (tag `phase0`).

Phase 0's goal (DESIGN.md §16): *fix a simple bug end-to-end against the
LiteLLM gateway with the reference Qwen3 models.* Met.

## Delivered

The full list is in [DESIGN.md §16.1](../DESIGN.md#161-phase-0-status). In
short:

- **Provider:** OpenAI-compatible streaming client for the LiteLLM gateway:
  tool calls, Qwen3 `<think>` handling (including Thinking-2507), typed errors
  with hints, retries, company TLS through the OS trust store or `caCertPath`.
- **Auth:** API key in the OS keychain (`cmcoder login`), checked on entry,
  shown only masked; ready for SSO later.
- **Agent loop:** tool-call repair and validation, loop guard, permission
  flow, max turns, context budget that keeps the session alive when the window
  fills.
- **Tools:** Read, Write, Edit, Glob, Grep, Bash (persistent Git Bash shell on
  Windows).
- **Permissions:** four modes, allow/deny rules, read-only command checks,
  protected paths, secret files, and (added at the end of Phase 0)
  **high-risk commands always ask**.
- **CLI:** interactive REPL, `-p` headless mode with JSON output, `doctor`,
  `login`/`logout`, `models`, `protocol-schema`.
- **Testing:** 218 tests (unit, CLI, TLS with a private CA, pseudo-terminal
  REPL, real LiteLLM proxy), 5 eval tasks, CI on Linux, macOS and Windows.

## Validation

| Where | Result |
|---|---|
| This repository (no company network) | Plan-to-code check, LiteLLM proxy integration test, private-CA TLS tests, Windows CI, adversarial review; see [DESIGN.md §16.2](../DESIGN.md#162-validation-of-the-plan-and-phase-0) |
| Your Windows machine, real gateway | `pytest`, `ruff`, `pyright` clean; `cmcoder login` key verified (4 models); `cmcoder doctor` all checks passed; `Qwen3.6-27B` native tool calls; **real-model evals 5/5**; interactive session used on a real project |

## Changed from the plan

- Python 3.11+ instead of 3.12+.
- Windows support moved from Phase 4 into Phase 0.
- Basic TUI with prompt_toolkit + rich; the Textual UI moves to Phase 1.
- 5 eval tasks instead of ~20; the rest move to Phase 1.
- Added after validation, at the security team's request: high-risk shell
  commands always need approval, in every mode (`core/risk.py`).

## Carried into Phase 1

Found during user trials ([DESIGN.md §16.3](../DESIGN.md#163-phase-1-backlog-from-user-trials-windows-real-gateway-qwen36-27b)):

1. The permission prompt's options get scrolled off screen by long previews.
2. Qwen uses Bash for file work instead of Read/Write/Edit.
3. The real context window is unknown (the gateway doesn't expose `/model/info`).

Also: the example gateway hostname in `README.md` and `docs/` still needs your
decision (replace it with a placeholder or keep it).

## Guides

- [python-guide.md](python-guide.md): the Phase 0 code for a day-1 Python developer.
- [langgraph-guide.md](langgraph-guide.md): the Phase 0 code mapped to LangChain/LangGraph.
