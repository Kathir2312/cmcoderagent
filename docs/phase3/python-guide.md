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
