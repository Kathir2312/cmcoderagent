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

**Status: done.** The 60-step session runs against a mock server that enforces
the 32K limit. Found and fixed on the way:
- the user's request was lost on a second compaction (it's now carried word
  for word);
- a long turn could only recover from one server "context too long" error
  (the retry is now per model call, and an estimate that failed is never
  trusted again);
- summaries could turn instructions found in files into "user requests" (the
  summariser is told tool output is data).

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

**Status: done.** Also fixed a second cause found by that test: option 2
printed the suggested rule, which for a multi-line command is the whole
command; it's now shown as one shortened line.

### 3. Detect the real context window

**Why:** the gateway doesn't expose `/model/info`, so cmcoder guesses 32K (§16.3 item 3).

**What:**
- Read the limit from the server's "maximum context length is N tokens" error
  and cache it per model (as `/model/info` results are cached today).
- `contextWindow` in `modelProfiles` still overrides.
- `cmcoder doctor` shows where the value came from (server, error, profile, default).

**Done when:** tests cover the vLLM/LiteLLM error formats; `doctor` reports the source.

**Status: done.** Beyond the plan:
- a deliberate **probe** finds the window *before* anything fails. It runs
  once per model and is cached 30 days, or 1 day if the server didn't say;
- tested through a real LiteLLM proxy.

Change from Phase 0: an explicit `contextWindow` in `modelProfiles` now beats
`/model/info`, so a wrong gateway value can be corrected.

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

**Status: built; waiting for the first real-gateway measurement.**
- Prompt rule and tool descriptions are in place.
- `core/steer.py` redirects plain file work: one simple command only, never
  pipelines or chains; sending the same command again runs it.
- `steerBashFileWork: false` turns it off per model.
- The evals report the tool-choice share, with two new tasks (`create-file`,
  `inspect-files`).

Run `uv run python evals/run.py` on the gateway and agree a target from the
"Tool choice" line.

**Measured on the real gateway (Windows, Qwen3.6-27B): 100%**: 58 file-tool
calls, 0 attempts at file work through Bash. Target set at **≥ 95%**.

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

**Status: done.** Example file: `docs/managed-settings.example.json`. Beyond
the plan:
- the managed-only keys are ignored in every other file, so a repository
  can't lock you to its own server;
- a forbidden mode fails before anything is sent to the gateway;
- the startup banner shows "policy: managed settings in effect".

The Windows path code runs only on Windows: CI exercises it, and it falls
back to `C:\Program Files` on any error.

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

**Status: done.**
- Interrupted (Ctrl+C) and crashed (half-written file) sessions both load as
  valid transcripts.
- Windows paths are normalised (`C:\Repo` = `c:\repo`).
- `/clear` keeps the old conversation resumable, and `-p` runs are saved too.
- New settings: `persistSessions` (default true; managed settings can turn it
  off) and `cleanupPeriodDays` (default 30).

### 7. Checkpoints and `/rewind`

**What:** file contents saved before every Write/Edit; `/rewind` restores code,
the conversation, or both, to an earlier user message (DESIGN.md §10).

**Done when:** tests restore files after edits, including a file the agent
created (rewind deletes it) and one it changed outside the project (asks first).

**Status: done.**
- Content-addressed snapshots next to the session file; in memory without one.
- Choices: code and conversation, conversation only, or code only.
- The rewound message is put back in the input line.
- Bash changes aren't tracked, and the screen says so.

### 8. TodoWrite

**What:** a `TodoWrite` tool that keeps a visible task list for multi-step
work, shown in the REPL and kept across compaction.

**Status: done.** A ☑ ◐ ☐ checklist in the REPL and `/todos`. No permission
needed. Kept in the compaction summary and restored on `--resume`.

### 9. Small/fast model jobs

**What:** use `smallFastModel` (Qwen3 ~7B) for session titles, compaction
summaries and other side jobs, so the main model's window is spent on the task.

**Status: done.**
- Compaction summaries (item 1) and session titles: a background job after
  the first turn, with thinking off and 40 tokens.
- Each falls back cleanly (main model / first message).
- The title never delays or fails a turn.

### 10. Tool-call robustness

**What:** a prompted tool-call fallback for models or gateways without native
tool calling, more argument repair, and edit-format variants (e.g. search/replace
blocks) if evals show Edit failures.

**Status: done.**
- `<tool_call>` text from a backend without a tool parser is parsed and run,
  and kept off the screen.
- Prompted mode (`toolCalling: "prompted"`) uses Qwen's own format, and is
  switched on automatically when the backend rejects `tools`.
- Python-style arguments are repaired.
- An Edit with a near-miss `old_string` is told the closest matching lines.
- Edit-format variants aren't built: no eval has shown Edit failures. Revisit
  if the real-gateway evals do.

### 11. Textual TUI

**What:** move the REPL to Textual: fixed input area, scrollable history,
permission dialog as a fixed-height panel with a scrollable preview (completes
item 2), status bar with context usage. The prompt_toolkit REPL stays as a
fallback (`--simple-ui`) until the Textual one is proven on Windows.

**Status: done, opt-in.** `cmcoder --tui` or `"ui": "textual"`.
- The classic REPL stays the default (instead of a `--simple-ui` flag) until
  the TUI is verified on Windows terminals.
- The permission dialog is a fixed-size modal with a scrollable preview,
  which completes item 2.
- `/resume` and `/rewind` are still classic-only.
- Tested headlessly with Textual's pilot (on all OSes) and in a
  pseudo-terminal.

### 12. More evals

**What:** grow from 5 to about 20 tasks (multi-file edits, test fixing, search,
refactors, Windows path cases), run against the real gateway before closing the
phase.

**Status: done; real-gateway run pending.**
- 20 tasks, each with a mock script that solves it with real tool calls; CI
  runs all 20 on Linux, macOS and Windows.
- `tests/test_evals.py` checks every task is well-formed and that its check
  fails on the untouched repo.
- Checks are Windows-safe (`$PYTHON`, `\r` stripped).

---

## Not in Phase 1

VS Code extension (Phase 2); MCP, hooks, subagents, Bash sandbox (Phase 3);
SSO, OpenTelemetry, Windows sandboxing (Phase 4).

## Checklist

- [x] 1. Auto-compaction
- [x] 2. Permission prompt never scrolls off screen (the Textual dialog follows in item 11)
- [x] 3. Detect the real context window
- [x] 4. Steer Qwen away from Bash for file work (real-gateway measurement pending)
- [x] 5. Managed settings
- [x] 6. Sessions and resume
- [x] 7. Checkpoints and `/rewind`
- [x] 8. TodoWrite
- [x] 9. Small/fast model jobs
- [x] 10. Tool-call robustness
- [x] 11. Textual TUI (opt-in until verified on Windows)
- [x] 12. More evals (20 tasks)
- [ ] Real-gateway run on Windows (`doctor`, evals) and `STATUS.md`
  - [x] `pytest` on Windows: 390 passed, 14 skipped, 0 failed
  - [x] `doctor`: all checks passed. Both models (Qwen3.6-27B, and
    Qwen3.5-35B-A3B as the small model) stream, switch thinking off and do
    native tool calling. The server doesn't state its context limit, so
    `contextWindow` is set in settings.
  - [x] evals: 18/20 at first; both failures were check/display issues, not
    the model's answers:
    - `inspect-files`: the right answer, but with a Windows `\`. Tools now
      always show `/`.
    - `add-test`: valid pytest-style tests that the check only ran with
      unittest. It now accepts both.
    - Effectively 20/20.
  - [ ] hands-on check (checklist, `/rewind`, `-c`/`/resume`, `--tui`)
    - `/resume` and small-model titles work ("Ping for connection test").
    - `--tui` works on Windows: the fixed dialog, buttons visible, status bar.
    - Found: the "2 Always" rule was relative to `E:\`, so cmcoder took the
      whole drive as the project root, probably because of a stray
      `E:\.cmcoder` (created by an earlier "Always" answer while running in
      `E:\`). Fixed: a drive root or the home folder is never a project root,
      and `~/.cmcoder` (the user's config) is not a project marker. The
      banner and `doctor` show the project root when it isn't the current
      folder.
    - `/rewind` works: the created file was deleted and the message put back.
      Found: rewinding to message 1 left an empty conversation, so `-c` had
      nothing to continue. Fixed: the pre-rewind conversation is now kept as a
      resumable "Before rewind: …" session, and the `-c` error is clearer.
