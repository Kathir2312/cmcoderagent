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
