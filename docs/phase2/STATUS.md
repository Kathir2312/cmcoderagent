# Phase 2 — VS Code: status

**Status:** ✅ complete. **Snapshot:** tag `phase2` (the commit that adds this file).

Phase 2's goal ([PLAN.md](PLAN.md)): *a VS Code extension on the same Python
engine as the CLI; the same task behaves the same in both.* Met.

## Delivered

All 13 items in [PLAN.md](PLAN.md):

1. **`cmcoder --protocol stdio`:** a long-lived agent speaking JSON lines;
   permission prompts, interrupts, mode/model switches, IDE tools, sessions.
2. **TypeScript protocol types** generated from the pydantic models; CI fails
   if they drift.
3. **The extension** (`vscode/`), run from `cmcoder` on PATH (or `uv run`).
4. **Chat panel:** streaming Markdown, tool cards with output, todo list,
   usage, Stop/Esc, mode picker.
5. **Permission prompts** in the chat: Allow / Always / Deny with a message.
6. **Diff review** in VS Code's diff editor with Accept / Reject.
7. **Editor context:** active file, selection and problems with each message;
   Ask About Selection (Ctrl+Alt+L); `@` to mention a file.
8. **IDE tools:** `getDiagnostics`, `openFile`.
9. **Sessions:** History (shared with the CLI) and resume.
10. **Open in Terminal.**
11. **Tests and CI:** Python, extension unit tests (with the panel in
    Chromium), a real-VS Code integration test, the `.vsix` as an artifact.
12. **Parity:** `evals/run.py --via both`; 20/20 tasks identical through the
    CLI and the extension's protocol.
13. **Docs:** extension README, guides sections 1–12.

## Validation

| Where | Result |
|---|---|
| CI (Linux, macOS, Windows; 10 jobs) | 446 Python tests, 9 extension tests, real-VS Code test, mock evals 40/40 with parity 20/20, security job: all green |
| Security review ([SECURITY-REVIEW.md](SECURITY-REVIEW.md)) | Bandit, Semgrep (892 rules), pip-audit, npm audit; 7 issues fixed (3 high); 0 known-vulnerable dependencies |
| Your Windows machine, real gateway (Qwen3.6-27B) | Fresh clone; `.vsix` built locally; CLI and VS Code hands-on checklist passed: chat, todo list, permission cards, diff editor Accept/Reject, editor context, Stop, History, Open in Terminal |

## Changed from the plan

- `system_init` serves as the handshake (no separate `ready` event).
- Added from the security review: **project trust** (a repository's
  `.cmcoder` settings can't set gateways, and need `cmcoder trust` for `env`,
  allow rules and permissive modes), Windows program lookup that never uses
  the project folder, and a CI `security` job.
- Building the `.vsix` needs Node.js 22+ (the packaging tool, `@vscode/vsce`
  4). Using the extension needs only VS Code and `cmcoder`.

## Carried into Phase 3

- Upgrade Node.js to 22 on the build machine (the hands-on build used
  `@vscode/vsce` 3.2.1 on Node 20 as a stop-gap; restore `package.json` with
  `git checkout -- vscode/package.json vscode/package-lock.json`).
- Optionally run the real-VS Code test on the Windows CI runner too.
- The TUI default decision (still opt-in) and the context window
  confirmation from Phase 1.

## Guides

- [python-guide.md](python-guide.md): the Phase 2 code for a Python developer.
- [langgraph-guide.md](langgraph-guide.md): the Phase 2 code mapped to LangChain/LangGraph.
