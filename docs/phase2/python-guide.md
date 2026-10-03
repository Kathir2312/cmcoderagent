# cmcoder Phase 2, explained for Python developers

This guide grows as Phase 2 is built, in the same style as the
[Phase 1 guide](../phase1/python-guide.md). Phase 2 adds a VS Code extension,
so later sections include some TypeScript; each one is explained from a
Python developer's point of view.

Each section follows the same pattern: **the problem**, **the idea**, **the
code**, **new Python ideas**, **the tests**, **try it**. Status of each
section follows [PLAN.md](PLAN.md).

---

## 1. `cmcoder --protocol stdio`

*Done.* Code: `src/cmcoder/cli/stdio.py` (new), `src/cmcoder/protocol/messages.py`
(new), new events in `src/cmcoder/protocol/events.py`, the `--protocol` option
in `cli/main.py`. Tests: `tests/test_stdio.py`.

### The problem

The VS Code extension is written in TypeScript, but the agent is Python. We
don't want two agents (they'd drift apart), so the extension must *run* the
Python agent and talk to it. `cmcoder -p` isn't enough: it answers one
prompt and exits, and it can't ask the user for permission.

### The idea

Run cmcoder as a **long-lived child process** and exchange **one JSON object
per line** ("JSON Lines", or NDJSON) over its stdin and stdout. This is how
Claude Code's own VS Code extension, and the Language Server Protocol, work.

```
extension  ──stdin──▶  {"type": "user_message", "text": "fix the bug"}
           ◀─stdout──  {"type": "assistant_delta", "text": "Let me"}
           ◀─stdout──  {"type": "permission_request", "request_id": "a1b2", "name": "Edit", …}
           ──stdin──▶  {"type": "permission_response", "request_id": "a1b2", "allow": true}
           ◀─stdout──  {"type": "result", "subtype": "success", …}
```

The events are the same ones `-p --output-format stream-json` already
writes, plus five new ones. Both directions are **pydantic models**, so
messages are validated and the TypeScript types can be generated from them
(item 2).

### The code

**`protocol/messages.py`**: the client's messages (`UserMessage`,
`Interrupt`, `PermissionResponse`, `SetMode`, `SetModel`, `Shutdown`) as a
*discriminated union*: `Field(discriminator="type")` lets pydantic pick the
right class from the `"type"` field, so `parse_message(line)` returns, say, a
`PermissionResponse`, or raises `ValidationError`.

**`cli/stdio.py`**, class `StdioServer`:

1. `serve()` builds the agent (as the CLI does) with `ask=self.ask`, emits
   `system_init` (the handshake), and starts a **reader thread** for stdin.
2. The main loop takes lines from an `asyncio.Queue` and calls `handle()`.
3. A `user_message` starts the turn as a separate **task**
   (`asyncio.create_task(self._run_turn(text))`), so the loop keeps reading
   stdin while the turn runs. That's what makes `interrupt` and
   `permission_response` possible mid-turn.
4. When the agent needs approval it calls `self.ask(req)`. `ask` creates an
   `asyncio.Future`, stores it under a new `request_id`, emits a
   `permission_request` event and `await`s the future. When the matching
   `permission_response` arrives, `handle()` calls `future.set_result(...)`
   and the agent continues.
5. `interrupt` cancels the turn task. The agent's own `CancelledError`
   handling repairs the history (as with Ctrl+C in the CLI); the server
   emits a `result` with subtype `interrupted`.
6. EOF on stdin or `shutdown` stops any turn, closes the agent (which saves
   the session) and exits with 0.

`run_stdio()` keeps the real stdout for events and points `sys.stdout` at
stderr, so a stray `print()` anywhere can never corrupt the protocol.

### New Python ideas

- **`asyncio.Future`** as a "mailbox": one coroutine awaits it, another
  fills it with `set_result()`. Cancelling the awaiting task cancels the wait.
- **Threads feeding asyncio**: `loop.call_soon_threadsafe(queue.put_nowait, line)`
  is the safe way for a thread to hand data to the event loop. Reading stdin
  in a thread works the same on Windows and Unix (asyncio's pipe readers
  don't support Windows stdin). It is a **daemon** thread, so a read that
  never returns doesn't stop the process from exiting.
- **Tasks for concurrency**: `create_task` runs the turn "in the background"
  of the same event loop; `task.cancel()` raises `CancelledError` inside it at
  its next `await`.

### The tests

`tests/test_stdio.py` starts the real `cmcoder --protocol stdio` with
`asyncio.create_subprocess_exec`, against the mock model server, and plays
the extension's part: deny with feedback, allow with "always" (checks the
saved rule), a second message while busy, a todo list, an interrupt during a
permission request, a late answer to a cancelled request, mode and model
switches, invalid messages, startup errors, and resuming with a todo list.
Every stdout line must parse as a protocol event.

```bash
uv run pytest tests/test_stdio.py -v
```

### Try it

Talk to it by hand. Each line you type is a message:

```bash
cmcoder --protocol stdio
{"type": "user_message", "text": "list the python files"}
```

You'll see the events stream back; answer a `permission_request` with
`{"type": "permission_response", "request_id": "<id>", "allow": true}`.
Ctrl+D (Ctrl+Z then Enter on Windows) ends it.

---

## 2. Generated TypeScript protocol types

*Done.* Code: `src/cmcoder/protocol/typescript.py`. Output:
`vscode/src/protocol.ts`. Tests: `tests/test_protocol_ts.py`.

### The problem

The extension is TypeScript. If its idea of an event (`{"type": "tool_result",
"is_error": …}`) differs from Python's, it breaks at run time, often silently
(a field that's `undefined`).

### The idea

The pydantic models stay the **single source of truth**. pydantic can describe
them as **JSON Schema** (`TypeAdapter(Event).json_schema()`); a small function
turns that schema into TypeScript `interface`s. A test compares the checked-in
`protocol.ts` with a fresh generation, so a protocol change that forgets the
TypeScript side fails CI.

### The code

`ts_type(schema)` maps one schema to a TypeScript type, recursively:
`"string"` → `string`, `"integer"` → `number`, `anyOf: [string, null]` →
`string | null`, `enum` → a union of literals, `array` → `T[]`, an open
object → `Record<string, unknown>`. `_interface()` writes one model; the
discriminated union becomes `export type AgentEvent = SystemInit | … | Result`.
TypeScript then **narrows** on `type`, just like `isinstance` in Python:

```ts
function onEvent(ev: AgentEvent) {
  if (ev.type === "tool_result") {
    ev.is_error;  // TypeScript knows ev is a ToolResult here
  }
}
```

### New Python ideas

- **JSON Schema from pydantic** as an exchange format between languages.
- **Golden-file tests**: generated output is committed, and a test checks it
  matches (`assert file == generate()`), with the fix in the failure message.

### Try it

```bash
uv run cmcoder protocol-schema --typescript | less
```

Add a field to `ModeChanged` in `events.py` and run
`uv run pytest tests/test_protocol_ts.py`: it fails and tells you the command
that regenerates the file.

---

## 3. Extension skeleton

*Done.* Code: `vscode/package.json`, `vscode/src/extension.ts`,
`vscode/src/agentProcess.ts`, `vscode/esbuild.mjs`. Tests:
`vscode/test/agentProcess.test.ts`.

### TypeScript for Python developers (just enough)

| Python | TypeScript |
|---|---|
| `pyproject.toml` | `package.json` (dependencies, scripts, and for an extension, what it contributes to VS Code) |
| `uv sync` / `uv.lock` | `npm ci` / `package-lock.json` |
| type hints + pyright | types are part of the language; `tsc --noEmit` only type-checks |
| `async def` / `await` | `async function` / `await`, with `Promise` instead of coroutines |
| `subprocess` / `asyncio.create_subprocess_exec` | `child_process.spawn` with callbacks on `stdout.on("data")` |
| `pytest` | `node --test` (built into Node) |

The code is **bundled** with esbuild into two files: `dist/extension.js`
(runs in VS Code's Node.js) and `dist/webview.js` (runs in the chat panel,
a sandboxed browser page).

### The code

- **`package.json` → `contributes`** declares, without running any code, the
  activity-bar icon, the chat view, the commands, the keybinding
  (Ctrl+Alt+K) and the settings. VS Code loads the extension when the view
  is opened (`activate()` in `extension.ts`).
- **`AgentProcess`** is the TypeScript twin of the Python test helper in
  `tests/test_stdio.py`: it spawns `cmcoder --protocol stdio`, splits stdout
  into lines (`lines()`, which keeps partial lines between chunks), parses
  each as an event, and `send()`s messages as JSON lines. `stop()` sends
  `shutdown`, closes stdin, and kills the process only if it hasn't exited
  after 5 seconds. A missing executable (`ENOENT`) becomes a clear message,
  not an exception.
- It uses **no `vscode` API**, so it runs in plain Node tests.

### A bug the TypeScript tests found in the Python side

The extension's test closes stdin right after `shutdown`. One run in three,
cmcoder died with **SIGABRT**. CPython aborts at exit if a daemon thread is
blocked inside a *buffered* read of stdin, because the buffer's lock is
still held. The reader thread now uses `os.read(fd, 65536)` on the raw file
descriptor (no buffer, no lock) and splits lines itself.
`test_shutdown_never_aborts` repeats the shutdown 8 times.

### The tests

```bash
cd vscode
npm ci
uv run --project .. npm test     # `uv run` puts cmcoder's Python on PATH
```

They start the mock model server and the real `cmcoder` and check a
permission prompt (allowed, then denied), a missing executable, and a
crash at startup.

---

## 4. Chat panel

*Done.* Code: `vscode/src/chatView.ts` (extension side),
`vscode/src/webview/main.ts` and `vscode/media/chat.css` (the page),
`vscode/src/webviewMessages.ts` (messages between the two).

### The idea

A **webview** is a small web page inside VS Code. It can't start processes
or read files; it can only exchange messages with the extension
(`postMessage`). So there are three parties:

```
chat page  ◀── postMessage ──▶  extension (chatView.ts)  ◀── stdin/stdout ──▶  cmcoder
```

The extension relays protocol events to the page unchanged
(`{kind: "event", event}`) and turns the page's actions into protocol
messages (`{kind: "send", text}` → `{"type": "user_message", …}`).

### The code

- `ChatViewProvider.resolveWebviewView()` sets the page's HTML with a strict
  **Content Security Policy**: only the bundled script (with a random
  `nonce`) may run, and nothing is loaded from the network.
- The page (`main.ts`) is plain DOM code: `onEvent()` is one `switch` over
  `ev.type`, the same shape as the REPL's event handler in `cli/repl.py`.
  Replies stream in through `assistant_delta` and are re-rendered as
  Markdown at most once per frame (`requestAnimationFrame`).
- **Model output is untrusted.** Raw HTML in a reply is shown as text
  (marked's `html` renderer is replaced by `escapeHtml`), and links are
  opened by the extension in the browser, only for `http(s)`.

### Try it

Install the `.vsix` (see the main README), open a project, press Ctrl+Alt+K
and ask something. **cmcoder: Show Log** shows cmcoder's stderr.

---

## 5. Permission prompts

*Done* (with item 4). `permissionCard()` in `webview/main.ts` shows the
request with a preview (the command, the start of the file to write, or the
Edit as `-`/`+` lines) and Allow / Always allow / Deny, plus a box for "what
to do instead". The answer goes back as a `permission_response`; on the
Python side it resolves the `Future` the agent is waiting on (section 1).
High-risk commands come with `can_remember: false`, so there's no
"Always allow" button. The mode picker sends `set_mode`; cmcoder answers with
`mode_changed` (or an error if the organisation forbids that mode).

---

## 6. Native diff review

*Done.* Code: `Tool.proposed_change` and `FileChange` in `src/cmcoder/tools/base.py`,
`WriteTool`/`EditTool.proposed_change` and `_replace` in `tools/files.py`,
`change` on `PermissionRequest` (agent and protocol), `vscode/src/diffReview.ts`.

### The problem

The permission card shows an Edit as `-`/`+` lines without context. For a
real review you want the whole file, with syntax highlighting, in the diff
view you already use for git.

### The idea

The **agent** works out what the file would become; the extension only
displays it. The extension must not re-implement Edit's rules (exact match,
uniqueness, CRLF handling); if it did, the diff could show something
different from what Edit then writes.

### The code

- `EditTool.run` used to apply the edit inline. That logic moved into
  `_replace(text, args)`, which returns either a `_Replaced` (the new text,
  how many matches, where) or an error message. `run` and `proposed_change`
  both call it, so the preview *is* the edit.
- `proposed_change` returns `FileChange(path, before, after)`, or `None` when
  the edit would fail or the file is over 1 MB.
- The agent attaches it to the `PermissionRequest`, the stdio server puts it
  in the event.
- `DiffReview` (TypeScript) is a **TextDocumentContentProvider**: VS Code
  asks it for the text of `cmcoder-diff:/after/app.py?id=…&side=after`, and
  it answers from memory. The file name stays at the end of the path so VS
  Code picks the right language. `vscode.diff` opens the two sides.
- `package.json` adds **Accept** / **Reject** buttons to the title bar of
  editors whose `resourceScheme == cmcoder-diff`. Both call
  `ChatViewProvider.answer()`, the same function the chat card uses.

### New Python ideas

- **Refactor to share logic between "preview" and "do".** A function that
  computes the result without side effects (`_replace`) plus a thin function
  that applies it (`run`) is easy to test: `tests/test_tools.py` checks that
  the preview equals what was written, including a CRLF file and
  `replace_all`.
- **A `str | _Replaced` return type** with `isinstance` to tell them apart:
  a lightweight alternative to exceptions for expected failures.

---

## 7. Editor context

*Done.* Code: `IdeContext` in `protocol/messages.py`, `format_ide_context` in
`src/cmcoder/core/ide.py`, `Agent.run(prompt, context=...)`,
`vscode/src/editorContext.ts`.

### The idea

What you're looking at is often the most useful context: "why is this
failing?" means *this* file and *these* selected lines. The extension sends
them with the message; the agent turns them into a note for the model.

### The code

- The extension collects the active file, the selection (with 1-based
  lines; a selection ending at column 0 doesn't count that line) and the
  file's errors and warnings (`vscode.languages.getDiagnostics`).
- `format_ide_context` writes them as a `<system-reminder>` block with paths
  relative to the project, the selection capped at 8,000 characters and at
  most 30 problems. It ends with "use it only if it is relevant", because
  the open file is often unrelated to the question.
- `Agent.run(prompt, context)` puts the note **before** the prompt in the
  user message. The session title still comes from the prompt alone, and
  `history()` strips the note when a conversation is shown again.

### Try it

Select a few lines, press Ctrl+Alt+L (Ask cmcoder About Selection) and
send. The 📎 line under your message shows what was attached.

---

## 8. IDE tools

*Done.* Code: `GetDiagnosticsTool`, `OpenFileTool`, `IDE_TOOLS` in
`core/ide.py`; `ide_call` in `cli/stdio.py`; `runIdeTool` in
`vscode/src/editorContext.ts`.

### The idea

Some tools only make sense inside an editor: "what does the Problems panel
say?" is faster and more accurate than running a linter, and "open this
file at line 42" shows the user something. These tools run in **VS Code**,
but the model calls them like any other tool.

### The code

- `_IdeTool` is a normal `Tool` whose `run` calls back into the stdio server
  (`ide_call`). It sends an `ide_tool_request` event and awaits a future,
  the same pattern as permission requests (section 1), with a 30-second
  timeout so a stuck editor can't hang the turn.
- The tools are only added when the client sends `ide_capabilities`; the
  agent rebuilds the tool list for every model call, so adding tools
  mid-session just works. In the CLI they don't exist.

### New Python ideas

- **Dependency injection with a callable**: the tool gets `IdeCall`, a
  function type (`Callable[[str, dict], Awaitable[ToolResult]]`), instead
  of knowing about the server. In tests you can pass any async function.

---

## 9. Sessions in VS Code

*Done.* Code: `ListSessions`/`SessionList`/`History` in the protocol,
`history()` in `cli/stdio.py`, the History panel in `webview/main.ts`.

The CLI's sessions (Phase 1, item 6) are reused as they are. The panel asks
for the list (`list_sessions`); resuming one **restarts** cmcoder with
`--resume <id>`, exactly what the CLI does, and the new process starts with a
`history` event so the panel can show the conversation. Tool calls are shown
by their labels, computed with each tool's `describe()` from the saved
arguments.

---

## 10. Open in terminal

*Done.* `openTerminal` in `vscode/src/extension.ts`.
`vscode.window.createTerminal({ shellPath: command, shellArgs: args })` runs
the program **directly** as the terminal's process, the same idea as
`subprocess.run([...])` without `shell=True`: no quoting problems with
spaces in paths.

---

## 11. Tests and CI

*Done.* Three layers, from fast to realistic:

| Layer | Runs | What it proves |
|---|---|---|
| Python (`pytest`) | everywhere | protocol, context formatting, IDE tool calls, history, `proposed_change` == the edit |
| Extension unit (`npm test`) | 3 OSes in CI | `AgentProcess` with the real cmcoder; the chat panel's rendering and messages in Chromium |
| Real VS Code (`npm run test:integration`) | Linux CI (xvfb) | chat → Edit → diff editor → Accept → file changed; `getDiagnostics` answered by VS Code |

The panel test loads the built `dist/webview.js` into a blank page with a
fake `acquireVsCodeApi()` that records messages, then posts events to it,
like the extension would. It checks, among other things, that HTML in a
reply is **not** turned into elements.

The integration test gets the extension's API from `activate()`'s return
value (`onEvent`, `send`), so it can drive the chat without clicking inside
the webview.

---

## 12. Parity check

*Done.* `evals/run.py --via both`.

The phase's goal is "the same task behaves the same in the CLI and VS
Code". The eval runner now has two drivers: `run_cli` (`cmcoder -p`) and
`run_stdio` (the extension's protocol, answering permission requests with
"deny", like `-p`). With `--via both` each task runs twice, and any
difference in pass/fail, status, number of turns or tool calls is reported
as a parity mismatch and fails the run. With the scripted model: 40/40
passed, 20/20 identical.

```bash
uv run python evals/run.py --mock --via both     # CI
uv run python evals/run.py --via both            # your real gateway
```
