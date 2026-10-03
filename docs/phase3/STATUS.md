# Phase 3 — Extensibility: status

**Status:** ✅ complete. **Snapshot:** tag `phase3` (the commit that adds this file).

Phase 3's goal ([PLAN.md](PLAN.md)): *teams can customise cmcoder without
forking it: tools (MCP), automatic checks (hooks), shortcuts (slash
commands), helpers (subagents) and know-how (skills), through files, working
the same in the CLI and in VS Code.* Met.

## Delivered

All 8 items in [PLAN.md](PLAN.md):

1. **MCP client** on the official `mcp` SDK: local (stdio) and remote
   (Streamable HTTP, SSE) servers from your settings, a trusted project's
   `.mcp.json`, or `--mcp-config FILE`; tools as `mcp__server__tool` through
   the permission engine; resources; `cmcoder mcp list|add|remove|approve`,
   `/mcp`; managed allow/deny lists.
2. **Hooks** in Claude Code's format on 8 events (`PreToolUse`,
   `PostToolUse`, `UserPromptSubmit`, `SessionStart`, `Stop`, `SubagentStop`,
   `PreCompact`, `Notification`), run with Git Bash on Windows; exit 2 or JSON
   decides; `allowManagedHooksOnly`.
3. **Custom slash commands** (`.cmcoder/commands`, `~/.cmcoder/commands`,
   namespaces, `$ARGUMENTS`/`$1`, `allowed-tools` for one turn) and MCP
   prompts as commands; in `/help`, completed in the REPL, the TUI and
   VS Code; `-p "/command"` too.
4. **Subagents** (`Task` tool): built-in `general-purpose` and `explore`,
   your own and a trusted project's agent files; same permissions, prompts,
   hooks and checkpoints as the main agent; steps shown inside the Task call.
5. **Skills**: `SKILL.md` folders, descriptions in the prompt, the `Skill`
   tool loads the rest on demand.
6. **Front ends**: VS Code completion, `/mcp`, subagent cards; `doctor` and
   `trust` list everything and what trust enables.
7. **Evals and security review**: three new eval tasks (command, subagent,
   MCP), [SECURITY-REVIEW.md](SECURITY-REVIEW.md).
8. **Docs**: [customising.md](customising.md), guide sections 1–7,
   [TESTING.md](TESTING.md) with `try-phase3.ps1`.

## Validation

| Where | Result |
|---|---|
| CI (Linux, macOS, Windows; 10 jobs), last run `6b3e303` | 511 Python tests (the setup script test on Windows PowerShell 5.1 only), 11 extension tests, real-VS Code test, mock evals 46/46 with parity 23/23 (`-p` vs the VS Code protocol), security job: all green |
| Security review ([SECURITY-REVIEW.md](SECURITY-REVIEW.md)) | Bandit, Semgrep (898 rules), pip-audit (67 packages), npm audit; 2 issues fixed (1 medium: repository files symlinked outside the project, dating from Phase 1); 0 known-vulnerable dependencies |
| Your Windows machine, real gateway (Qwen3.6-27B) | `docs/phase3/TESTING.md` checklist reported done. Seen in the run: the `tickets` MCP server's tool (including a ticket that doesn't exist, handled cleanly) and the `reviewer` subagent (3 tool calls, report with both bugs in `app.py`) |

## Changed from the plan

- **Bash sandbox moved to Phase 4** (decided at the start), to be built for
  Linux, macOS and Windows together.
- **Project skills don't need trust**: like `CMCODER.md` they are text and
  grant nothing. MCP servers, hooks and agents from a repository do.
- **`model` in a slash command's frontmatter is not used**; a command can ask
  for a subagent with its own model instead. New setting `subagentModel`
  (used by `explore`; falls back to `smallFastModel`, then the main model).
- **Added**: `--mcp-config` and `-p "/command"` (for the evals, and as in
  Claude Code); a mock-server `expect` check for evals.
- **Fixed on the way** (found by CI on Windows): every subprocess now gets
  its own stdin, which stopped a hang in VS Code when a subagent started
  (`4f2c1cb`); files written by Windows editors (BOM, CRLF) are read
  correctly; a flaky chat panel test.

## Carried into Phase 4

- The Phase 4 plan: SSO if needed, other gateway API formats, OpenTelemetry,
  a standalone binary and per-platform `.vsix`, the Bash sandbox.
- Small parity gaps in VS Code: `/rewind`, `/cost`, `/model`, `/todos` are
  terminal-only (the panel has controls for the rest).
- Semgrep stays a manual step while its registry is unreachable from the
  development environment.
- From Phase 2: Node.js 22 on the build machine (or keep using the CI
  `.vsix`); the TUI default decision.

## Guides

- [python-guide.md](python-guide.md): the Phase 3 code for a Python developer.
- [langgraph-guide.md](langgraph-guide.md): the Phase 3 features mapped to LangChain/LangGraph.
- [customising.md](customising.md): examples of every extension, and the trust table.
