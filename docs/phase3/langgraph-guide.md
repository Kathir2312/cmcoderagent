# cmcoder Phase 3, through LangChain / LangGraph eyes

This guide grows as Phase 3 is built. Read the
[Phase 2 LangGraph guide](../phase2/langgraph-guide.md) first. Status of each
section follows [PLAN.md](PLAN.md).

## Preview of the mapping

| Phase 3 item | LangChain / LangGraph equivalent |
|---|---|
| 1. MCP client | `langchain-mcp-adapters`: `MultiServerMCPClient`, `load_mcp_tools` |
| 2. Hooks | Callbacks / middleware (`before_model`, `after_model`, tool call wrappers) |
| 3. Slash commands | Prompt templates (`ChatPromptTemplate`), LangSmith prompt hub |
| 4. Subagents | Subgraphs; "supervisor" and "agents as tools" patterns |
| 5. Skills | Retrieval of instructions on demand (tool that loads docs) |

---

## 1. MCP client

### The LangGraph way

```python
from langchain_mcp_adapters.client import MultiServerMCPClient

client = MultiServerMCPClient({
    "github": {"url": "https://mcp.example.com/github", "transport": "streamable_http"},
    "files": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
              "transport": "stdio"},
})
tools = await client.get_tools()          # LangChain BaseTool objects
agent = create_react_agent(model, tools)
```

### The cmcoder way

| langchain-mcp-adapters | cmcoder |
|---|---|
| `MultiServerMCPClient({...})` | `McpManager(settings, project_root)` from `mcpServers` / `.mcp.json` |
| `await client.get_tools()` | `manager.start()` then `manager.tools()` (cmcoder `Tool`s) |
| tool name = the server's | `mcp__<server>__<tool>`, so servers can't collide or impersonate built-in tools |
| tool runs when the model calls it | the permission engine asks first (allow rules per server or tool) |
| a server failing raises | a failing server is reported and skipped |

### Why they differ

cmcoder runs on a developer's machine with their permissions, so a server is
also a security boundary: project servers need trust and approval, program
lookup avoids the project folder, tokens come from `${VAR}`, and every call
is a permission decision. In a LangGraph app these are usually your
deployment's concern.

### Exercise

Load the demo server (`tests/mcp_servers/demo_server.py`) with
`MultiServerMCPClient` in a small LangGraph agent, and with
`cmcoder mcp add demo -- python tests/mcp_servers/demo_server.py`. Compare
what each shows the model as the tool's name and schema.

---

## 2. Hooks

### The LangGraph way

LangChain v1 agents have **middleware**: functions that run before/after the
model or wrap tool calls (`before_model`, `after_model`, `wrap_tool_call`), and
can change state or stop the run. Callbacks (`on_tool_start`, ...) observe
without changing anything.

### The cmcoder way

| LangChain / LangGraph | cmcoder hooks |
|---|---|
| `wrap_tool_call` middleware that refuses | `PreToolUse` hook, exit 2 or `permissionDecision: "deny"` |
| middleware that edits the tool result | `PostToolUse` hook output added to the result |
| `before_model` middleware adding context | `UserPromptSubmit` / `SessionStart` stdout |
| `after_model` middleware with `jump_to="model"` | `Stop` hook that blocks: the model keeps working |
| Python functions in your app | shell commands in settings: any language, no code change |

### Why they differ

cmcoder's users configure it, they don't program it: hooks are commands in a
JSON file, so a team can share them in a repository. That's also why a
repository's hooks need trust and approval before they run.

## 3. Custom slash commands

### The LangGraph way

LangChain has **prompt templates** (`ChatPromptTemplate.from_template("Review
{file}")`) filled in code, and LangSmith's prompt hub to share them. MCP
servers' prompts can be loaded with `langchain-mcp-adapters`
(`load_mcp_prompt`).

### The cmcoder way

| LangChain / LangGraph | cmcoder |
|---|---|
| `PromptTemplate` with `{file}` | a Markdown file with `$1` / `$ARGUMENTS` |
| prompt hub / a Python module | `.cmcoder/commands/` in the repository, `~/.cmcoder/commands/` for you |
| `load_mcp_prompt(session, "review", arguments=...)` | `/mcp__server__review app.py` |
| tools bound for one call (`bind_tools`) | `allowed-tools`: allow rules for one turn |

### Why they differ

The person at the keyboard picks a command; the program doesn't. So commands
are files with a name, a description and completion, and the only thing they
can change besides the prompt is the permission rules for that turn, which a
repository gets only when the project is trusted.
