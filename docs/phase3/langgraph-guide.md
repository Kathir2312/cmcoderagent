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

## 4. Subagents

### The LangGraph way

A **subgraph**: a compiled graph used as a node, or called from a tool
(`deepagents`' `task` tool does exactly this). With its own state schema, the
subgraph's messages don't flow into the parent's state; streaming with
`subgraphs=True` shows its steps with a namespace.

### The cmcoder way

| LangChain / LangGraph | cmcoder |
|---|---|
| subgraph with its own `MessagesState` | `spawn_subagent`: a new `Agent` with its own `messages` |
| `deepagents` `task` tool, `subagents=[...]` | `Task` tool, built-in and `.cmcoder/agents/*.md` agents |
| `stream(..., subgraphs=True)` namespaces | `parent_tool_use_id` on the forwarded events |
| a different `model` per subagent | `model: small` / a model name in the agent file |
| `interrupt()` inside the subgraph | the same `ask` function: the prompt appears as usual |

### Why they differ

cmcoder's agents are files a team writes, not code, and a subagent must obey
the same permission rules as the main agent, so it shares the policy object
instead of getting its own graph configuration.

## 5. Skills

### The LangGraph way

There's no built-in "skill"; the usual patterns are a retrieval tool over a
document store (RAG), or dynamic prompts (middleware that adds instructions
when a condition holds). `deepagents` reads skills from a filesystem backend
in the same SKILL.md format.

### The cmcoder way

| LangChain / LangGraph | cmcoder |
|---|---|
| a retriever tool over how-to documents | the `Skill` tool, by name instead of by similarity search |
| dynamic system prompt middleware | the "# Skills" list: names and descriptions only |
| documents in a vector store | folders in the repository or `~/.cmcoder/skills` |

### Why they differ

A team's how-tos are few and named, so the model can pick by description
without embeddings, and the files live next to the code where they're
reviewed like code.

## 6–7. Front ends, evals and security

| LangChain / LangGraph | cmcoder |
|---|---|
| LangGraph Platform API + your UI | the Agent Protocol (`--protocol stdio`) + the VS Code panel |
| `stream_mode=["updates", "messages"]` | protocol events (`tool_use`, `assistant_delta`, `command_list`, ...) |
| LangSmith datasets and evaluators | `evals/tasks/*` and `evals/run.py`, with a `-p` vs VS Code parity check |
| (your deployment's job) | trust + approval for anything a repository can run, reviewed in SECURITY-REVIEW.md |
