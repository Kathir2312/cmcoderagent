# M2: tools and MCP on LangChain

The second step of the Phase 8 engine ([plan](../../project/phases/phase8/PLAN.md)):
cmcoder's tools, and the tools of MCP servers, become LangChain tools that
LangGraph's tool node can run. The code is in `src/cmcoder/engine/`
(`tools.py`, `mcp.py`, `context.py`); the tests in `tests/test_engine_tools.py`
and `tests/test_engine_mcp.py`.

**You learn:** what a LangChain tool is and how its schema reaches the model;
`ToolMessage`, its `status` and `artifact`; `ToolException`; LangGraph's
`ToolNode`; the runtime context (`context_schema`, `get_runtime`);
`langchain-mcp-adapters`.

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

## 4. MCP through `langchain-mcp-adapters`

The adapters turn MCP tools into LangChain tools, two ways:

| | `MultiServerMCPClient(...).get_tools()` | `load_mcp_tools(session)` |
|---|---|---|
| Sessions | by default a new one, and a new server process, **for every call** (the spike's E5) | the session you give it, for as long as you keep it |
| cmcoder uses | — | this, on `McpManager`'s own lasting session per server |

So `McpManager` keeps what the adapters don't do (approving project
servers, a log file per server, cmcoder's TLS and proxy, timeouts) and
`engine/mcp.py` wraps each adapted tool (`McpServerTool`):

- the name `mcp__<server>__<tool>` (the adapters' prefix would give
  `<server>_<tool>`): permission rules match these names;
- the result as text: the adapters return content blocks
  (`[{"type": "text", …}]`), the gateway takes a string;
- errors with `status="error"`: with `handle_tool_errors=True` the adapters
  hand an error back as an ordinary result, so cmcoder turns that off and
  catches the error itself.

The adapters need the `mcp` library 1.x, so cmcoder moved to it (design
decision D18); its own MCP client got a small `_Client` wrapper for that.

## Exercises

1. Make a tool with `@tool` from a function with a docstring and typed
   arguments, and print `convert_to_openai_tool(it)`. Where does each part
   of the schema come from?
2. In the lab's step 2, change `handle_tool_error` to `False` on the tool:
   what happens to the missing-file call?
3. In step 3, run the graph without `context=`: what does the tool answer,
   and why?
4. Replace `mcp_tools(...)` in step 4 with
   `MultiServerMCPClient({"demo": {...}}).get_tools()` and call
   `mcp__demo__shout` three times: how many server processes start? (The
   spike's [e5_mcp_adapters.py](../../project/phases/phase8/spike/e5_mcp_adapters.py)
   counts them.)
