# Phase 1 — Daily driver: plan

**Goal:** comfortable for daily use on a real repository with the company
gateway and Qwen3 models (DESIGN.md §16).

**Starts from:** the Phase 0 snapshot (commit `ba6669f`, tag `phase0`).

**How we work:** one item at a time, in the order below. Each item:

1. gets your OK before coding starts;
2. ships with tests and with its section in [python-guide.md](python-guide.md) and
   [langgraph-guide.md](langgraph-guide.md);
3. passes `pytest`, `ruff`, `pyright` and the mock evals in CI on Linux, macOS and Windows;
4. is ticked off in the checklist at the end of this file.

When all items are done, `STATUS.md` is written, this folder is frozen, and the
commit is tagged `phase1`.

---

## Items, in order

### 1. Auto-compaction

**Why first:** the 32K context window is the binding constraint. Today
cmcoder only blanks old tool outputs when the window fills, so long tasks lose
context (DESIGN.md §9, §16.2).

**What:**
- When the prompt reaches a set share of the window (default 80%), summarise
  older turns into one message and keep the most recent turns as they are.
- `/compact [focus]` runs it by hand, e.g. `/compact keep the failing test names`.
- The summary is written by the small/fast model if one is configured, else the
  main model; it keeps the user's goals, decisions, files touched and open problems.
- Tool-call ids stay paired (no orphaned tool results).
- Falls back to Phase 0 blanking if summarising fails.

**Done when:** a scripted 60-step session in the mock server runs to the end
inside a 32K window; `/compact` works; the summary prompt is covered by tests.

### 2. Permission prompt never scrolls off screen

**Why:** user-trial bug (§16.3 item 1): long previews push the 1/2/3 options
out of view.

**What:**
- Cap the preview height (first and last lines plus "… N more lines"); `v` shows
  the full text.
- Options always printed after the preview and repeated in the input line
  (`1 yes · 2 always · 3 no:`).
- Invalid input (e.g. `11`) re-shows the options.

**Done when:** a pseudo-terminal test with a 500-line heredoc shows the options
on the last screen in an 80×24 terminal.

### 3. Detect the real context window

**Why:** the gateway doesn't expose `/model/info`, so cmcoder guesses 32K (§16.3 item 3).

**What:**
- Read the limit from the server's "maximum context length is N tokens" error
  and cache it per model (as `/model/info` results are cached today).
- `contextWindow` in `modelProfiles` still overrides.
- `cmcoder doctor` shows where the value came from (server, error, profile, default).

**Done when:** tests cover the vLLM/LiteLLM error formats; `doctor` reports the source.

### 4. Steer Qwen away from Bash for file work

**Why:** Qwen3.6 writes files with heredocs and reads them with `python -c`,
bypassing read-before-write checks and causing item 2 (§16.3 item 2).

**What:**
- Tune the system prompt and tool descriptions for the Qwen profile.
- Detect common patterns (`cat > f <<`, `echo … > f`, `python -c "open(…)"`,
  `cat file`, `sed -n`) and answer with a short "use the Write/Read tool"
  message instead of a permission prompt.
- New evals that score which tools the model chose.

**Done when:** on the real gateway, the tool-choice evals show Read/Write/Edit
used for file work in most runs (target agreed after the first measurement).

### 5. Managed settings (enterprise policy)

**Why:** the security team wants rules developers can't loosen (moved from
Phase 4).

**What:**
- An admin-only file that wins over every other settings layer:
  - Linux: `/etc/cmcoder/managed-settings.json`
  - macOS: `/Library/Application Support/cmcoder/managed-settings.json`
  - Windows: `C:\Program Files\cmcoder\managed-settings.json`, found through the
    Windows API, not an environment variable. (Not `C:\ProgramData`, where
    ordinary users can create folders.)
- No environment variable or flag can move or switch it off.
- **Fails closed:** a broken or unreadable file stops cmcoder with a clear message.
- Keys it can enforce:
  - `permissions.disableBypassPermissionsMode: "disable"`, enforced at
    start-up, in `/mode` and on Shift+Tab;
  - `permissions.highRiskCommands: "deny"`;
  - `permissions.deny` rules that always apply;
  - `permissions.allowManagedPermissionRulesOnly`: ignore allow rules from
    other layers and "always allow" answers;
  - `lockProviders`: only the providers (gateway URL, CA) defined in the managed
    file can be used;
  - `env`.
- `cmcoder doctor` shows the managed file and warns if users can write to it.

**Done when:** tests prove each key cannot be overridden by user/project/local
settings, flags or env vars, and a broken file stops start-up.

**Limit:** this stops developers loosening the rules; it does not protect a
compromised machine (an admin-level attacker can edit the file). That needs the
sandbox/VM and gateway-side controls discussed separately.

### 6. Sessions and resume

**What:** each conversation saved as JSONL under
`~/.cmcoder/projects/<project-hash>/<session-id>.jsonl` (owner-only
permissions, API keys never written); `--continue`, `--resume [id]` and a
`/resume` picker; old sessions cleaned up after `cleanupPeriodDays` (DESIGN.md §10).

**Done when:** a session interrupted mid-tool resumes with a valid transcript;
tests on Windows paths.

### 7. Checkpoints and `/rewind`

**What:** file contents saved before every Write/Edit; `/rewind` restores code,
the conversation, or both, to an earlier user message (DESIGN.md §10).

**Done when:** tests restore files after edits, including a file the agent
created (rewind deletes it) and one it changed outside the project (asks first).

### 8. TodoWrite

**What:** a `TodoWrite` tool that keeps a visible task list for multi-step
work, shown in the REPL and kept across compaction.

### 9. Small/fast model jobs

**What:** use `smallFastModel` (Qwen3 ~7B) for session titles, compaction
summaries and other side jobs, so the main model's window is spent on the task.

### 10. Tool-call robustness

**What:** a prompted tool-call fallback for models or gateways without native
tool calling, more argument repair, and edit-format variants (e.g. search/replace
blocks) if evals show Edit failures.

### 11. Textual TUI

**What:** move the REPL to Textual: fixed input area, scrollable history,
permission dialog as a fixed-height panel with a scrollable preview (completes
item 2), status bar with context usage. The prompt_toolkit REPL stays as a
fallback (`--simple-ui`) until the Textual one is proven on Windows.

### 12. More evals

**What:** grow from 5 to about 20 tasks (multi-file edits, test fixing, search,
refactors, Windows path cases), run against the real gateway before closing the
phase.

---

## Not in Phase 1

VS Code extension (Phase 2); MCP, hooks, subagents, Bash sandbox (Phase 3);
SSO, OpenTelemetry, Windows sandboxing (Phase 4).

## Checklist

- [ ] 1. Auto-compaction
- [ ] 2. Permission prompt never scrolls off screen
- [ ] 3. Detect the real context window
- [ ] 4. Steer Qwen away from Bash for file work
- [ ] 5. Managed settings
- [ ] 6. Sessions and resume
- [ ] 7. Checkpoints and `/rewind`
- [ ] 8. TodoWrite
- [ ] 9. Small/fast model jobs
- [ ] 10. Tool-call robustness
- [ ] 11. Textual TUI
- [ ] 12. More evals
- [ ] Real-gateway run on Windows (`doctor`, evals) and `STATUS.md`
