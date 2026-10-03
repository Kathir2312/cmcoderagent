# cmcoder Phase 3, explained for Python developers

This guide grows as Phase 3 is built, in the style of the
[Phase 2 guide](../phase2/python-guide.md). Status of each section follows
[PLAN.md](PLAN.md).

---

## 1. MCP client

*Done.* Code: `src/cmcoder/mcp_client.py`, `src/cmcoder/cli/mcp_cmd.py`,
`McpServerConfig` and the approvals in `config/settings.py`. Tests:
`tests/test_mcp.py`, with a real server in `tests/mcp_servers/demo_server.py`.

### The problem

Every team has its own systems: an issue tracker, a database, internal APIs.
Building a cmcoder tool for each would never end. **MCP** (Model Context
Protocol) is a standard way for a program to offer tools to any AI agent:
write an MCP server once, and every MCP client can use it.

### The idea

cmcoder is an MCP **client**. For each configured server it opens a
connection (a child process speaking JSON-RPC over stdin/stdout, or an HTTP
endpoint), asks for its tools, and wraps each one as an ordinary cmcoder
`Tool`. From then on the agent loop doesn't know the difference: the model
calls `mcp__demo__add`, the permission engine checks it, and `McpTool.run`
forwards the call to the server.

### The code

- **`McpManager`** holds the servers. `start()` runs at the beginning of the
  first turn; for a project's server it first checks the approval (and asks
  through the agent's permission prompt if needed).
- **One task per server** (`_run`): it enters `async with Client(...)`,
  lists the tools, signals "ready", and then *waits* until cmcoder closes.
  This is needed because the SDK uses anyio task groups, which must be
  entered and exited in the same task. Other tasks can still call
  `client.call_tool(...)`, because the connection's own tasks do the I/O.
- **`McpTool`** overrides `spec()` to hand the model the server's own JSON
  schema, and uses `McpArgs` (a `ToolInput` with `extra="allow"`), so any
  arguments pass through to the server, which validates them.
- **`content_text()`** turns MCP content blocks (text, images, resources,
  structured content) into text for the model.
- **Security details:** the program is found with `find_program` (never the
  project folder on Windows), its stderr goes to a log file, `${VAR}` values
  are expanded from the environment at start time (so tokens aren't stored),
  and a project's server must be trusted and approved for its exact
  configuration (`config_fingerprint`).

### New Python ideas

- **Long-lived async context managers in a background task**, with an
  `asyncio.Event` for "ready" and another for "stop": a common way to keep a
  connection open across many calls.
- **`ExceptionGroup`** (Python 3.11): anyio raises groups of errors;
  `_describe()` digs out the first real one for the user.
- **Dynamic tools**: `self.name` is set per instance instead of as a class
  constant.

### The tests

```bash
uv run pytest tests/test_mcp.py -v
```

A real MCP server (written with the same SDK, `MCPServer` and `@server.tool()`)
runs over stdio and over HTTP; the mock model calls its tools; the tests
cover errors, a missing program, a crashing server, `${VAR}`, approvals,
managed allow/deny lists, and allow rules for a whole server.

### Try it

```bash
cmcoder mcp add demo -- python tests/mcp_servers/demo_server.py
cmcoder mcp list --check
cmcoder          # then: "use the demo server to add 2 and 40"
```

---

## 2. Hooks

*Done.* Code: `src/cmcoder/core/hooks.py` (`HookRunner`), hook points in
`core/agent.py` (`_hook`, `_run_call`, `run`, `compact`). Tests:
`tests/test_hooks.py`.

### The problem

Teams have rules the model should follow every time: run the formatter after
an edit, never touch `migrations/`, add the ticket number to every prompt.
Writing them in `CMCODER.md` is a request; a hook is a **guarantee**, because
it's code that runs whether or not the model remembers.

### The idea

At fixed points of the agent loop (before and after each tool call, when a
prompt is submitted, when the model wants to stop, ...), cmcoder runs your
commands and listens to their answer: exit code 2 means "no", JSON can say
allow/ask/deny, and other output can be added to the model's context.

### The code

- `HookRunner.__init__` flattens the settings into `Hook` objects (event,
  matcher, command, origin). Managed hooks are read separately so they can't
  be overwritten by merging; `allowManagedHooksOnly` drops the others.
- `run(event, payload, match=...)` runs each matching hook with
  `asyncio.create_subprocess_exec(bash, "-c", command)`, sends the payload as
  JSON on stdin with `communicate()`, and enforces a timeout (killing the
  whole process tree). The answers of several hooks are combined into one
  `HookOutcome`; the strictest permission decision wins.
- In the agent, `_run_call` runs `PreToolUse` **after** the deny rules have
  been checked, so a hook's "allow" can never undo a deny rule; it only
  replaces an `ASK` (and never for high-risk commands).
- `Stop`: when the model answers without tool calls, a blocking hook adds its
  reason as a `<system-reminder>` message and the loop `continue`s, at most
  three times per turn.

### New Python ideas

- **`asyncio.subprocess` with stdin**: `proc.communicate(input=bytes)` writes
  stdin, closes it and reads both outputs without deadlocking.
- **Combining decisions**: an ordering dict (`allow < ask < deny`) to pick the
  strictest answer.

### Try it

Add to `~/.cmcoder/settings.json`:

```json
{"hooks": {"PostToolUse": [{"matcher": "Write|Edit",
  "hooks": [{"type": "command", "command": "echo 'remember the changelog' >&2; exit 2"}]}]}}
```

Ask cmcoder to edit a file and watch the model react to the hook's message.

## 3. Custom slash commands

*Done.* Code: `src/cmcoder/core/commands.py`, `Agent.expand_command` in
`core/agent.py`, the `else:` branch of each front end's command handler
(`cli/repl.py`, `cli/tui.py`, `cli/stdio.py`). Tests: `tests/test_commands.py`.

### The problem

Teams type the same long prompts again and again ("review this file for our
security checklist, then ..."). A command saves that prompt in a file the team
can share, with a short name and arguments.

### The idea

A command is just text: `/review app.py` reads `review.md`, puts `app.py` in
place of `$1` / `$ARGUMENTS`, and runs the result as an ordinary prompt. The
only power a file adds is `allowed-tools`: allow rules that last one turn.

### The code

- `parse_file` splits simple `key: value` frontmatter from the body (no YAML
  library needed); `split_tools` splits `Read, Bash(a, b)` on commas outside
  parentheses.
- `_load_dir` turns `db/migrate.md` into `db:migrate` with
  `Path.relative_to(...).with_suffix("").parts`. `load_commands` loads the
  project's, then yours with `dict.update`, so yours win.
- `CommandSource.load` strips `allowed_tools` from an untrusted project's
  commands: a repository can't grant itself tools.
- `Agent.run(..., allow=[...])` wraps the real loop (`_run`) in
  `try/finally`, so the turn's rules are cleared even when the turn is
  interrupted (`aclose()` of an async generator runs its `finally`).
- MCP prompts: `client.get_prompt(name, arguments)` returns messages; their
  text becomes the prompt.

### New Python ideas

- **`shlex.split`**: splits arguments like a shell, so `"my file.py"` is one
  word. An unbalanced quote raises `ValueError`; we fall back to `str.split`.
- **`re.sub` with a function**: `$1`..`$9` are replaced by looking each number
  up, with `""` for a missing word.
- **`finally` in an async generator**: cleanup that runs however the consumer
  stops (end, exception, `break`, cancellation).

### Try it

```bash
mkdir -p ~/.cmcoder/commands
printf -- '---\ndescription: Explain a file\n---\nExplain $1 to a new team member.\n' > ~/.cmcoder/commands/explain.md
cmcoder        # then type /ex and press Tab, or /explain README.md
```
