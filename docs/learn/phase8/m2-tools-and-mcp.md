# M2: tools and MCP on LangChain

The second step of the Phase 8 engine ([plan](../../project/phases/phase8/PLAN.md)):
cmcoder's tools, and the tools of MCP servers, become LangChain tools that
LangGraph's tool node can run. The code is in `src/cmcoder/engine/`
(`tools.py`, `mcp.py`, `context.py`); the tests in `tests/test_engine_tools.py`
and `tests/test_engine_mcp.py`.

**You learn:** what a LangChain tool is and how its schema reaches the model;
`ToolMessage`, its `status` and `artifact`; `ToolException`; LangGraph's
`ToolNode`; the runtime context (`context_schema`, `get_runtime`);
LangChain's own MCP support, `langchain.mcp`.

**Lab:** [labs/m2_tools.py](labs/m2_tools.py), no model needed:

```
uv run python docs/learn/phase8/labs/m2_tools.py
```

## 1. A LangChain tool

A tool is a `BaseTool`: a `name`, a `description`, an `args_schema` (what
the model may send) and `_run`/`_arun` (what it does). The quick ways to
make one are `@tool` on a function and `StructuredTool.from_function`;
cmcoder subclasses `BaseTool`, because its tools already exist:

| `BaseTool` | cmcoder's `CmcoderTool` |
|---|---|
| `name`, `description` | from the cmcoder tool (`Read`, `Edit`, `Bash`, …) |
| `args_schema`: a pydantic class, or a JSON schema | the JSON schema the classic engine sends (`Tool.spec()`), so the model sees what it saw before |
| `_arun(**args)` | checks the arguments with the tool's pydantic `Input` (the same error text as before), runs the tool, cuts the output to the limit |
| `response_format="content_and_artifact"` | returns `(text, {"summary": …})`: the text goes to the model, the artifact (the one-line summary) stays for the UI |
| `handle_tool_error=True` + `ToolException` | a failed tool becomes a `ToolMessage` with `status="error"`, which the model sees |

On the way to the model LangChain makes two changes, both harmless: it
inlines shared definitions (`$defs`/`$ref`, as in TodoWrite's items) and
drops `title` keys (cmcoder's own tools have none; MCP servers send them).
The tests compare the schemas after doing the same.

## 2. Invoking a tool

`tool.ainvoke({"file_path": "app.py"})` returns the content alone.
`tool.ainvoke({"name": "Read", "args": {...}, "id": "call_1", "type": "tool_call"})`,
a tool call as the model makes it, returns a `ToolMessage` with the
`tool_call_id`, `status` and `artifact`: this is what goes back into the
conversation.

## 3. `ToolNode` and the runtime context

LangGraph's `ToolNode` (in `langgraph.prebuilt`) takes the last
`AIMessage`, runs its tool calls (in parallel) and adds the `ToolMessage`s
to the state. In M4 `create_agent` builds one for us.

A tool often needs to know about the *run*: cmcoder's need the working
folder, the project, the shell. LangGraph's answer is the **runtime
context**: a graph is built with `context_schema=EngineContext` and each run
gets its own, `graph.ainvoke(state, context=EngineContext(tool_ctx))`. Inside
a node or tool, `langgraph.runtime.get_runtime().context` gives it back. So
one set of tool objects serves every run and every folder; the lab's step 3
runs the same tools in two projects. (LangChain 1.x also lets a tool
function declare a `runtime: ToolRuntime` parameter that is filled in for
it; cmcoder's adapter reads it with `get_runtime()` because it wraps
existing tools.)

## 4. MCP through `langchain.mcp`

LangChain ships its own MCP support in `langchain.mcp` (in `langchain` 1.4.6,
marked beta: importing it warns that the API may still change). It runs on
**FastMCP**'s client and the `mcp` library 2.x:

| `langchain.mcp` | What it does |
|---|---|
| `MCPAdapter(target)` | Connects to a server (an `https://` URL, a `Path` to a script, a FastMCP transport or client, or an `MCPConfig` of several) and `list_tools()` gives LangChain tools for `create_agent` |
| `as_langchain_tool(tool, client)` | One MCP tool as a LangChain tool, called through `client` |
| `list_tools(cache_mode=...)` | Reuses a cached tool list (SEP-2549), for servers on the 2026 protocol |
| Elicitation | A server that asks the user for input in the middle of a call is answered through a LangGraph `interrupt()`, so the run pauses for the user and resumes |

A plain string target must be an `http(s)` URL: a string naming a local
script would otherwise start that script, so a local server is asked for
explicitly (a `Path` or a transport).

cmcoder's `McpManager` holds one FastMCP client per server, open for the
whole session, and keeps what an agent library leaves to the application:
approving project servers, the program found on PATH (never one in the
project), a log file per server (the stdio transport's `log_file`), cmcoder's
TLS and proxy for HTTP servers (the transport's `httpx_client_factory`),
timeouts. `engine/mcp.py` turns each tool into a LangChain tool on that
client (`as_langchain_tool`) and wraps it (`McpServerTool`):

- the name `mcp__<server>__<tool>` (permission rules match these names);
- the result as text: `langchain.mcp` gives content blocks
  (`[{"type": "text", …}]`), the gateway takes a string;
- a stopped server, a timeout or a broken connection become an error result,
  like a tool's own failure, which `langchain.mcp` already makes one.

The connection matters: a FastMCP client that isn't held open connects for
each call, and with `keep_alive=False` that starts the server again every
time (the spike's [E5b](../../project/phases/phase8/spike/e5b_langchain_mcp.py)
counts it).

**Two things to know about FastMCP.** When it is imported it reads
`FASTMCP_*` settings from a `.env` file in the current folder (for cmcoder,
the user's project) and logs to the terminal. cmcoder imports it with both
switched off (`mcp_client.fastmcp_client`), only for that moment, because the
MCP servers it starts inherit its environment.

**Not used yet:** elicitation needs a checkpointer (the run has to pause and
resume), which comes in M5; until then cmcoder's clients don't offer it, so
servers don't ask.

**History:** on 15 October cmcoder first went through
`langchain-mcp-adapters` (`MultiServerMCPClient`, `load_mcp_tools`), which
needs the `mcp` library 1.x; `langchain.mcp`, in the same LangChain release,
was missed, and replaced it on 16 October (design decision D18).

## Exercises

1. Make a tool with `@tool` from a function with a docstring and typed
   arguments, and print `convert_to_openai_tool(it)`. Where does each part
   of the schema come from?
2. In the lab's step 2, change `handle_tool_error` to `False` on the tool:
   what happens to the missing-file call?
3. In step 3, run the graph without `context=`: what does the tool answer,
   and why?
4. Replace `mcp_tools(...)` in step 4 with
   `MCPAdapter(Path(DEMO_SERVER))` and `await adapter.list_tools()`, and call
   `shout` three times outside `async with adapter:`. How many server
   processes start, and what changes with a `StdioTransport(...,
   keep_alive=False)` target? (The spike's
   [e5b_langchain_mcp.py](../../project/phases/phase8/spike/e5b_langchain_mcp.py)
   counts them.)
