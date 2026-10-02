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
