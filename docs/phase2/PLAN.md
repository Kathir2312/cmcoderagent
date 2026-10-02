# Phase 2 — VS Code: plan

**Goal** ([DESIGN.md §16](../DESIGN.md#16-roadmap)): a VS Code extension that
uses the same Python engine as the CLI. **Done when:** the same task behaves
the same in the CLI and in VS Code.

Phase 2 is done when cmcoder:

1. runs as a long-lived process speaking the Agent Protocol over stdio;
2. has a VS Code extension with a chat panel, permission prompts, native diff
   review for edits, and the editor's context (file, selection, problems);
3. passes the Python tests, the extension's tests and the evals in CI on
   Linux, macOS and Windows, and builds an installable `.vsix`;
4. has been used on a real project on your Windows machine through VS Code.

## Decisions

| Question | Decision | Why |
|---|---|---|
| How the extension uses the agent | Starts `cmcoder --protocol stdio` as a child process (DESIGN D7) | One engine; the CLI and VS Code can't drift apart |
| Webview UI | **Plain TypeScript** and CSS, no React | One chat view doesn't need a framework; smaller bundle, fewer dependencies to review. (DESIGN §3 said React + Vite; changed here.) |
| Bundling | esbuild, one file each for the extension and the webview | Fast, no config |
| Distribution | An internal `.vsix`, built in CI; `code --install-extension cmcoder.vsix` | No marketplace account needed; works offline |
| Node.js | Needed only to **build** the extension. Installing the `.vsix` doesn't need it (VS Code has its own runtime) | |
| Where it lives | `vscode/` in this repository | One repo, one CI, protocol types generated from the Python source |
| cmcoder's settings | Stay in the same JSON files as the CLI. The extension has only its own few settings (path to `cmcoder`, auto-context on/off) and a command that opens the JSON files | One source of truth; no settings UI to keep in sync |

## Items, in order

### 1. `cmcoder --protocol stdio`

**What:** a long-lived process that reads JSON messages on stdin and writes
events on stdout, one per line. One conversation per process.

- Client → agent: `user_message`, `interrupt`, `permission_response`,
  `set_mode`, `set_model`, `shutdown`.
- Agent → client (added to the existing events): `permission_request`,
  `todo_update`, `mode_changed`, `model_changed`. The first event,
  `system_init`, is the handshake.
- Versioned (`protocol_version` in `system_init`), no VS Code-specific types.
- Logs and stray prints go to stderr only, never stdout.

**Done when:** tests drive a full conversation over a pipe: a turn,
a permission request answered both ways, an interrupt, a mode switch, a
second turn, and a clean shutdown on EOF.

**Status: done.** `src/cmcoder/cli/stdio.py`, `src/cmcoder/protocol/messages.py`,
`tests/test_stdio.py` (the real CLI as a subprocess against the mock server):

- one turn at a time (`busy` error otherwise);
- a permission answer can deny with feedback for the model, or allow with
  "always" (saved like the CLI's);
- `interrupt` cancels a pending permission request too;
- a resumed conversation (`--continue`, `--resume`) sends its todo list;
- startup errors are an `error` event with kind `startup`;
- stdin is read in a thread, because asyncio's stdin readers don't work with
  Windows pipes.

### 2. Generated TypeScript protocol types

**What:** `vscode/src/protocol.ts` generated from the Python models
(`cmcoder protocol-schema`). CI fails if it's out of date.

**Status: done.** `src/cmcoder/protocol/typescript.py`: a small generator from
the JSON Schema (no npm code generator to review). `cmcoder protocol-schema
--typescript` writes it; `tests/test_protocol_ts.py` fails when the file is
stale. Event fields are all required (the agent always sends them); message
fields with defaults are optional.

### 3. Extension skeleton

**What:** activation, finding `cmcoder` (setting `cmcoder.executable`, else
PATH), starting one process per workspace folder, restarting after a crash,
an output channel with the process's stderr, `npm run package` → `.vsix`.

**Status: done.** `vscode/src/extension.ts`, `agentProcess.ts`, `chatView.ts`:
- `cmcoder.executable` + `cmcoder.executableArgs` (e.g. `uv run --project …`),
  `cmcoder.permissionMode`; commands New Conversation, Stop, Open Chat
  (Ctrl+Alt+K), Show Log, Open Settings File;
- a missing `cmcoder` or a crash shows a message and a **Restart** button;
- `.vsix`: 22 KB. CI job "VS Code extension" type-checks, runs the tests on
  three OSes and uploads `cmcoder.vsix`.
- Found by the extension's tests: `cmcoder --protocol stdio` sometimes died
  with SIGABRT at shutdown (a daemon thread in a buffered stdin read). Fixed
  by reading the raw descriptor; a regression test runs shutdown 8 times.

### 4. Chat panel

**What:** a webview in the side bar: streaming replies (Markdown), tool
cards with the first lines of output, the todo checklist, token usage, a
stop button (and Esc), "New conversation".

**Status: done.** `vscode/src/webview/main.ts` + `media/chat.css`: plain DOM
code, VS Code theme colours. Model output is Markdown with raw HTML escaped;
the page's Content Security Policy allows only the bundled script; links
open in the browser only for http(s). Checked in Chromium with a scripted
conversation (screenshots in the session); an automated UI test comes with
item 11.

### 5. Permission prompts

**What:** Allow / Always / Deny (with a message for the model) inside the
chat panel, the mode picker, high-risk commands without "Always".

**Status: done** (built with the chat panel): a card per request with a
preview (the command, the file to write, or the Edit's -/+ lines), **Allow**,
**Always allow** (only when the request can be remembered; the tooltip shows
the rule), **Deny** with an optional message; cancelled cards when the turn
is interrupted.

### 6. Native diff review

**What:** a proposed Edit or Write opens VS Code's diff editor (current file
vs proposed). Accept makes the edit; Reject denies it, optionally with a
message.

### 7. Editor context

**What:** each message carries the active file, the selection and the
file's problems (can be turned off). "Ask cmcoder about selection" command;
`@` to attach a file.

### 8. IDE tools

**What:** `getDiagnostics` and `openFile`, offered to the model only when the
client says it supports them (`ide_tool_request` / `ide_tool_result`).

### 9. Sessions in VS Code

**What:** a list of this project's conversations (the same files the CLI
uses) and resume; "continue last conversation".

### 10. Open in terminal

**What:** a command that runs the CLI in VS Code's integrated terminal, for
those who prefer it.

### 11. Tests and CI

**What:** Python protocol tests; TypeScript unit tests of the protocol client
against a real `cmcoder` and the mock model server; an extension
integration test in a real VS Code (`@vscode/test-electron`, headless on
Linux); the `.vsix` as a CI artifact.

### 12. Parity check

**What:** the eval runner also drives tasks through `--protocol stdio`; the
results must match the CLI's.

### 13. Guides and install docs

**What:** install and use guide for the extension; `python-guide.md` and
`langgraph-guide.md` sections for each item; `STATUS.md` at the end.

## Not in Phase 2

MCP, hooks, custom slash commands, subagents, Bash sandbox (Phase 3); SSO,
OpenTelemetry, standalone binary, platform-specific VSIX, Windows sandboxing
(Phase 4). A settings UI (the extension opens the JSON files instead).

## Checklist

- [x] 1. `cmcoder --protocol stdio`
- [x] 2. Generated TypeScript protocol types
- [x] 3. Extension skeleton
- [x] 4. Chat panel
- [x] 5. Permission prompts
- [ ] 6. Native diff review
- [ ] 7. Editor context
- [ ] 8. IDE tools
- [ ] 9. Sessions in VS Code
- [ ] 10. Open in terminal
- [ ] 11. Tests and CI
- [ ] 12. Parity check
- [ ] 13. Guides and install docs
- [ ] Hands-on use in VS Code on Windows, and `STATUS.md`
