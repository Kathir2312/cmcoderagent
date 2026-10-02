# cmcoder through LangChain / LangGraph eyes (as of Phase 0)

> Frozen at the end of Phase 0 (commit `ba6669f`, tag `phase0`). For later
> phases see [docs/README.md](../README.md).

You are learning agentic AI with LangChain and LangGraph. cmcoder does **not**
use either library, but it is built from exactly the same ideas: a chat model
that can call tools, a loop, state, human approval, streaming and memory
management. This guide maps every cmcoder piece to the LangChain/LangGraph
concept you already know (or are learning), so reading the code teaches you how
those libraries work underneath.

Read [python-guide.md](python-guide.md) first if you are new to Python; this
guide assumes you know what a class, `async` and a generator are.

Contents:

1. [The one-minute version](#1-the-one-minute-version)
2. [Concept map](#2-concept-map)
3. [The graph that cmcoder's loop really is](#3-the-graph-that-cmcoders-loop-really-is)
4. [Piece by piece](#4-piece-by-piece)
5. [cmcoder written in LangGraph (sketch)](#5-cmcoder-written-in-langgraph-sketch)
6. [Why cmcoder doesn't use LangGraph](#6-why-cmcoder-doesnt-use-langgraph)
7. [What LangGraph gives you that cmcoder will build later](#7-what-langgraph-gives-you-that-cmcoder-will-build-later)
8. [Exercises](#8-exercises)

---

## 1. The one-minute version

If you have built the classic LangGraph **ReAct agent** (`create_react_agent`,
or a `StateGraph` with an `agent` node, a `tools` node and `tools_condition`),
you already know cmcoder's architecture:

```
           ┌──────────────┐  tool calls?  ┌──────────────┐
 START ──▶ │  call model  │ ────yes─────▶ │  run tools   │
           └──────────────┘               └──────┬───────┘
                  ▲  │ no                        │
                  │  ▼                           │
                  │ END                          │
                  └──────────────────────────────┘
```

In LangGraph you **declare** this graph and the library runs it. In cmcoder the
same graph is **written out by hand** as a `while True:` loop in
`Agent.run()` (`src/cmcoder/core/agent.py`). Same behaviour, no framework.

---

## 2. Concept map

| LangChain / LangGraph | cmcoder | Where |
|---|---|---|
| `ChatOpenAI(base_url=..., api_key=...)` | `OpenAICompatProvider` | `providers/openai_compat.py` |
| `model.bind_tools(tools)` | `stream_chat(model, messages, tool_specs, ...)` | `providers/openai_compat.py` |
| `HumanMessage` / `AIMessage` / `ToolMessage` / `SystemMessage` | `Message` with `role` = user / assistant / tool / system | `providers/messages.py` |
| `AIMessage.tool_calls` | `Message.tool_calls: list[ToolCall]` | `providers/messages.py` |
| `AIMessageChunk` while streaming | `TextDelta`, `ReasoningDelta`, `ToolCallStarted`, `StreamDone` | `providers/messages.py` |
| `@tool` / `BaseTool` with `args_schema` (pydantic) | `Tool` subclass with `Input` (pydantic) | `tools/base.py` |
| `convert_to_openai_tool(tool)` | `Tool.spec()` → `ToolSpec` → `to_wire_tools()` | `tools/base.py`, `openai_compat.py` |
| `ToolNode` | `Agent._run_call()` | `core/agent.py` |
| `ToolNode(handle_tool_errors=True)` | Errors returned to the model as `ToolResult(is_error=True)` | `core/agent.py` |
| `AIMessage.invalid_tool_calls` | `parse_tool_arguments()` repairs or reports bad JSON | `core/agent.py` |
| `tools_condition` (conditional edge) | `if not msg.tool_calls: ... return` | `Agent.run()` |
| `MessagesState` (graph state) | `Agent.messages` + `ToolContext` | `core/agent.py`, `tools/base.py` |
| `recursion_limit` (default 25) | `max_turns` (default 50) | `Agent.run()`, settings `maxTurns` |
| `interrupt()` + `Command(resume=...)` (human-in-the-loop) | `PermissionPolicy.check()` → `ask(PermissionRequest)` → `PermissionAnswer` | `core/permissions.py`, `core/agent.py` |
| `graph.astream(..., stream_mode="messages")` / `astream_events` | `Agent.run()` is an async generator yielding protocol events | `protocol/events.py` |
| `trim_messages` / `pre_model_hook` | `ContextBudget.free_space()` | `core/context.py` |
| `ChatPromptTemplate` / system prompt | `build_system_prompt()` + `CMCODER.md` memory files | `core/prompt.py` |
| `FakeListChatModel` / `GenericFakeChatModel` for tests | `MockServer` (a real HTTP server speaking the OpenAI API) | `testing/mock_server.py` |
| LangSmith run trace | Event stream (`-p --output-format stream-json`) | `cli/headless.py` |
| Checkpointer + `thread_id` | Not yet (sessions / resume are Phase 1) | — |
| Subgraphs / supervisor (multi-agent) | Not yet (subagents are Phase 3) | — |

---

## 3. The graph that cmcoder's loop really is

Here is `Agent.run()` with everything except the graph removed. Each comment
names the LangGraph equivalent.

```python
async def run(self, prompt):
    self.messages.append(Message.user(prompt))          # graph input: {"messages": [HumanMessage]}
    steps = 0
    while True:                                         # the graph's super-steps
        if steps >= self.max_turns:                     # recursion_limit
            yield result("max_turns"); return
        steps += 1

        budget.free_space(self.messages)                # pre_model_hook / trim_messages

        async for chunk in self.provider.stream_chat(   # node "agent": model.bind_tools(tools).astream(...)
            self.model, self.messages, self.tool_specs(), ...):
            yield chunk_as_event                        # stream_mode="messages"
        self.messages.append(msg)                       # reducer add_messages appends the AIMessage

        if not msg.tool_calls:                          # tools_condition → END
            yield result("success"); return

        for call in msg.tool_calls:                     # node "tools": ToolNode
            async for event in self._run_call(call):    #   (incl. the human-approval interrupt)
                yield event                             #   each ToolMessage appended to state
        # edge "tools" → "agent": loop again
```

Two things LangGraph does for you that cmcoder does by hand:

1. **Reducers.** In LangGraph, `MessagesState` uses the `add_messages` reducer,
   so a node returns `{"messages": [new_msg]}` and the framework appends it.
   cmcoder just calls `self.messages.append(...)`.
2. **Scheduling.** LangGraph decides which node runs next from the edges.
   cmcoder's `while` loop and `if` are the edges.

---

## 4. Piece by piece

### 4.1 The chat model: `ChatOpenAI` vs `OpenAICompatProvider`

With LangChain you would talk to the company LiteLLM gateway like this:

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    base_url="https://<your-gateway>/v1",
    api_key=os.environ["CMCODER_API_KEY"],
    model="Qwen3.6-27B",
    extra_body={"chat_template_kwargs": {"enable_thinking": True}},
)
```

`OpenAICompatProvider` does the same job (POST `/v1/chat/completions` with
`stream: true`, then read Server-Sent Events) but also handles things that a
generic wrapper leaves to you:

- **Qwen `<think>` blocks.** `providers/thinking.py` (`ThinkSplitter`) splits
  reasoning from the answer, including Thinking-2507 models that send only
  `</think>`. In LangChain you would write an output parser or read
  `reasoning_content` yourself.
- **Broken tool-call JSON.** Small models sometimes produce trailing commas,
  code fences or double-encoded JSON. LangChain puts these in
  `AIMessage.invalid_tool_calls`; cmcoder's `parse_tool_arguments()` repairs
  the common cases and otherwise tells the model what was wrong so it retries.
- **Gateway errors.** `classify_http_error()` turns LiteLLM responses into
  typed errors (`AuthFailed`, `ContextTooLong`, `RateLimited`, ...) with hints.
  LangChain surfaces `openai.APIStatusError` and leaves classification to you.
- **Company TLS.** `providers/transport.py` uses the OS trust store
  (`truststore`) or a CA file. With `ChatOpenAI` you would pass a custom
  `http_client=httpx.Client(verify=...)`.

### 4.2 Messages

LangChain's message classes and cmcoder's `Message` both mirror the OpenAI
wire format:

| OpenAI wire `role` | LangChain class | cmcoder |
|---|---|---|
| `system` | `SystemMessage` | `Message.system(text)` |
| `user` | `HumanMessage` | `Message.user(text)` |
| `assistant` (+ `tool_calls`) | `AIMessage(tool_calls=[...])` | `Message(role="assistant", tool_calls=[ToolCall...])` |
| `tool` (+ `tool_call_id`) | `ToolMessage(tool_call_id=...)` | `Message.tool_result(call_id, name, text)` |

The rule both enforce: **every tool call in an `AIMessage` must be answered by
a `ToolMessage` with the same id** before the next model call, or the server
rejects the request. That is why cmcoder has `_repair_after_interrupt()`: if
you press Ctrl+C mid-tool, it adds "Interrupted by the user." results for
the unanswered calls. In LangGraph you hit the same error if you resume a
thread whose last `AIMessage` has unanswered tool calls.

### 4.3 Tools: `@tool` vs `Tool`

LangChain:

```python
from langchain_core.tools import tool

@tool
def read_file(file_path: str, limit: int | None = None) -> str:
    """Read a text file."""
    ...
```

cmcoder (`tools/files.py`, simplified):

```python
class ReadInput(ToolInput):        # = args_schema
    file_path: str
    limit: int | None = None

class ReadTool(Tool):
    name = "Read"
    description = "Read a file ..."  # = the docstring
    Input = ReadInput
    read_only = True                 # extra: used by permissions

    async def run(self, args, ctx): ...           # = _arun
    def permission_target(self, args, ctx): ...   # extra: what permission rules see
    def describe(self, args, ctx): ...            # extra: "Read(src/app.py)" for the UI
```

Both turn the pydantic schema into the JSON Schema the model sees. cmcoder
adds three things an agent that edits real files needs: `read_only`,
`permission_target` and `describe`. There is also `ToolContext`, shared state
for tools (project root, which files have been read and when, so `Edit` can
refuse to change a file the model hasn't read or that changed on disk). In
LangGraph you would keep that in extra graph state keys and pass it with
`InjectedState`.

### 4.4 The tools node: `ToolNode` vs `_run_call`

`ToolNode` looks up each tool call, validates arguments, runs it and wraps the
output in a `ToolMessage`. `_run_call()` does the same in this order:

1. Unknown tool → error result listing the real tools.
2. Arguments not valid JSON → error result ("retry with valid JSON").
3. Arguments fail pydantic validation → error result with the field errors.
4. Same call repeated 3 times in a row → error result ("try something else").
   This loop guard has no LangGraph built-in; `recursion_limit` only stops the
   whole run.
5. **Permission check** (next section).
6. Run the tool; cut very long output (`truncate_middle`) so one result can't
   flood the context window.

Errors go back to the model as tool results rather than crashing, like
`ToolNode(handle_tool_errors=True)`. The model reads the error and corrects
itself; this is what makes small models usable.

### 4.5 Human-in-the-loop: `interrupt()` vs `ask()`

In LangGraph you pause a graph for approval with `interrupt()`:

```python
from langgraph.types import interrupt

def approve(state):
    call = state["messages"][-1].tool_calls[0]
    answer = interrupt({"tool": call["name"], "args": call["args"]})  # graph pauses here
    if answer != "yes":
        ...
```

and resume it later with `graph.invoke(Command(resume="yes"), config)`. This
needs a **checkpointer**, because the process might even restart between the
pause and the resume.

cmcoder pauses differently: it simply `await`s a callback.

```python
check = self.policy.check(tool, args, self.ctx)        # ALLOW / DENY / ASK
if check.decision == Decision.ASK:
    answer = await self.ask(PermissionRequest(...))    # REPL shows the 1/2/3 prompt
```

Because the agent and the terminal live in one process, awaiting is enough: no
checkpoint is needed. The REPL implements `ask` as a terminal prompt; the
future VS Code extension will implement it as a dialog. In headless mode
(`cmcoder -p`) there is nobody to ask, so `ask` is `None` and the call is
denied with a hint.

The decision itself (`core/permissions.py`) is a **policy function**, which
LangGraph doesn't provide at all; you would write it inside your approval
node. Its order of evaluation:

1. deny rules (e.g. `Bash(curl:*)`)
2. **high-risk commands** (`rm -rf`, `git reset --hard`, `curl … | sh`, …):
   always ask, in every mode, never remembered (`core/risk.py`)
3. protected paths (`.git/`, `.cmcoder/`)
4. allow rules (e.g. `Bash(npm test:*)`)
5. secret files (`.env`, `*.pem`, …)
6. the mode's default: `default`, `acceptEdits`, `plan`, `bypassPermissions`

`plan` mode is the read-only "think first" pattern that LangGraph tutorials
build as a separate planner node.

### 4.6 Streaming: `astream` vs an async generator

LangGraph:

```python
async for msg, meta in graph.astream(inputs, stream_mode="messages"):
    print(msg.content, end="")
```

cmcoder: `Agent.run()` **is** the stream. It's an async generator that yields
typed events (`protocol/events.py`): `AssistantDelta`, `ReasoningDelta`,
`ToolUse`, `ToolResult`, `PermissionDenied`, `UsageUpdate`, `Warning`,
`Error`, and finally `Result`. The REPL draws them, headless mode prints them
as JSON lines (`--output-format stream-json`), and the VS Code extension will
read the same JSON over stdio. This is cmcoder's version of LangGraph's
`astream_events` plus a stable, versioned schema (`cmcoder protocol-schema`).

### 4.7 Memory: `trim_messages` vs `ContextBudget`

Every agent eventually runs out of context window. LangChain gives you
`trim_messages(messages, max_tokens=..., strategy="last")`, which drops whole
old messages. That's risky for agents, because dropping an `AIMessage` but
keeping its `ToolMessage` (or the reverse) breaks the id rule from §4.2.

cmcoder's `ContextBudget` (`core/context.py`) is more careful:

- It estimates tokens from characters, then **calibrates** the estimate with
  the real `prompt_tokens` the server reports after each call.
- It sizes `max_tokens` for each request to the room actually left.
- When space runs out, it **blanks the contents** of the oldest large tool
  outputs (and large tool-call arguments, like whole files passed to `Write`)
  but keeps every message, so tool-call ids still pair up.
- If the server still says "context too long", it becomes more conservative
  and retries once.

Phase 1 adds **summarisation** (auto-compaction): the LangChain equivalent is
`ConversationSummaryMemory` or a summarise node in LangGraph.

### 4.8 Tests: fake chat models vs `MockServer`

LangChain tests usually swap the model for `GenericFakeChatModel`. cmcoder
goes one level lower: `MockServer` is a real local HTTP server that speaks the
OpenAI streaming API and replays scripted replies (text, tool calls, `<think>`
blocks, realistic token usage, error codes). The **whole** stack is tested:
HTTP, SSE parsing, think-splitting, tool calls, permissions, the loop.
There is also an opt-in test against a real LiteLLM proxy, and `evals/run.py`
runs real coding tasks against the real model (5/5 passing on your gateway).

---

## 5. cmcoder written in LangGraph (sketch)

To make the mapping concrete, here is roughly what cmcoder's core would look
like in LangGraph. It's **for learning only**, not part of cmcoder, and is
deliberately simplified (one tool, no streaming UI, no context management).

```python
import os
import subprocess
from typing import Literal

from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import Command, interrupt

from cmcoder.core.risk import high_risk_reason  # reuse cmcoder's checks!


@tool
def bash(command: str) -> str:
    """Run a shell command in the project folder and return its output."""
    r = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=120)
    return (r.stdout + r.stderr)[-20_000:]


TOOLS = {"bash": bash}
llm = ChatOpenAI(
    base_url=os.environ["CMCODER_BASE_URL"],
    api_key=os.environ["CMCODER_API_KEY"],
    model="Qwen3.6-27B",
).bind_tools(list(TOOLS.values()))


def agent(state: MessagesState):                       # cmcoder: provider.stream_chat(...)
    return {"messages": [llm.invoke(state["messages"])]}


def tools(state: MessagesState):                       # cmcoder: Agent._run_call(...)
    results = []
    for call in state["messages"][-1].tool_calls:
        command = call["args"].get("command", "")
        if risk := high_risk_reason(command):          # cmcoder: PermissionPolicy.check
            ok = interrupt({"command": command, "reason": risk})  # cmcoder: await ask(...)
            if ok != "yes":
                results.append(ToolMessage("The user denied this.", tool_call_id=call["id"]))
                continue
        output = TOOLS[call["name"]].invoke(call["args"])
        results.append(ToolMessage(output, tool_call_id=call["id"]))
    return {"messages": results}


def route(state: MessagesState) -> Literal["tools", "__end__"]:  # cmcoder: `if not msg.tool_calls`
    return "tools" if state["messages"][-1].tool_calls else END


g = StateGraph(MessagesState)
g.add_node("agent", agent)
g.add_node("tools", tools)
g.add_edge(START, "agent")
g.add_conditional_edges("agent", route)
g.add_edge("tools", "agent")
graph = g.compile(checkpointer=MemorySaver())          # needed for interrupt()

config = {"configurable": {"thread_id": "demo"}, "recursion_limit": 50}  # = max_turns
out = graph.invoke({"messages": [("user", "clean the build folder")]}, config)
if "__interrupt__" in out:                             # paused for approval
    print("Approve?", out["__interrupt__"][0].value)
    out = graph.invoke(Command(resume="yes"), config)
print(out["messages"][-1].content)
```

Compare this with `core/agent.py` and notice what the sketch is missing that
cmcoder has: streaming to the screen, read-before-edit checks, the loop guard,
repairing bad JSON, context budgeting, Windows shell handling (Git Bash,
process trees), TLS with the company CA, keychain storage of the API key, and
tests against a mock server. That gap is most of cmcoder's code.

---

## 6. Why cmcoder doesn't use LangGraph

This was a deliberate choice ([DESIGN.md](../DESIGN.md) D1, D6), not a rejection of
LangGraph:

1. **The graph is tiny.** A coding agent is one loop with two nodes. A
   `while` loop is easier to read, debug and step through than a compiled
   graph.
2. **Control over the details that matter here.** Qwen `<think>` handling,
   repairing tool-call JSON, sizing `max_tokens` per request and keeping tool
   ids paired during trimming all happen *inside* what LangChain treats as one
   `llm.invoke()` call. Owning that code made those fixes possible (several
   came from your real-gateway trials).
3. **Mirroring Claude Code.** Permission modes, rules and the event protocol
   follow Claude Code's behaviour exactly, which a general framework doesn't
   model.
4. **A small, auditable install.** Dev machines (including Windows) install
   only httpx, pydantic, typer, rich, prompt-toolkit, truststore, keyring and
   pathspec. That's easier for your security team to review than the
   LangChain dependency tree.
5. **Pause by awaiting.** Approval is a simple `await` inside one process, so
   no checkpointer is needed just to ask "Allow `rm -rf build`?".

When **would** LangGraph be the better choice? For workflows with many
branching steps, durable long-running jobs that must survive restarts,
several cooperating agents, or when you want LangSmith tracing and LangGraph
Platform deployment out of the box. Nothing stops a LangGraph app from
calling cmcoder as a tool (`cmcoder -p "..." --output-format json`).

---

## 7. What LangGraph gives you that cmcoder will build later

| LangGraph feature | cmcoder plan |
|---|---|
| Checkpointer + `thread_id` (resume a conversation) | Phase 1: sessions saved to disk, `--continue` / `--resume` |
| Summarise node / `ConversationSummaryMemory` | Phase 1: auto-compaction |
| Time travel (`get_state_history`, replay) | Phase 1: file checkpoints and `/rewind` |
| Subgraphs, supervisor, `Send` (parallel workers) | Phase 3: subagents (`Task` tool) with per-role models |
| `create_react_agent(..., tools=[...])` with MCP adapters | Phase 3: MCP client |
| Callbacks / LangSmith tracing | Phase 4: OpenTelemetry |
| LangGraph Server (HTTP API over the graph) | Phase 2: JSON-over-stdio protocol for the VS Code extension |

Reading those phases with LangGraph in mind is a good way to learn both: each
cmcoder feature is the "by hand" version of a LangGraph feature.

---

## 8. Exercises

1. **Trace a run.** Run
   `cmcoder -p "list the python files" --output-format stream-json --verbose`
   and match each JSON line to a node or edge in the §1 diagram.
2. **Find the conditional edge.** In `core/agent.py`, find the line that plays
   the role of `tools_condition`. What happens when the model replies with no
   tool calls and no text?
3. **Break the id rule.** In a LangGraph notebook, append an `AIMessage` with a
   tool call but no `ToolMessage`, then call the model through your gateway.
   Read the error, then read `_repair_after_interrupt()` in cmcoder.
4. **Build the §5 sketch** against your LiteLLM gateway (use a throwaway
   folder). Ask it to delete a folder and watch `interrupt()` pause the graph.
   Then do the same with cmcoder and compare the two approval prompts.
5. **Port a feature.** Add cmcoder's "same call 3 times in a row" guard to the
   §5 sketch, as a check in the `tools` node.
6. **Compare trimming.** Use `trim_messages` on a long tool-using
   conversation and check whether any `ToolMessage` lost its `AIMessage`. Then
   read `ContextBudget.free_space()` and explain why it blanks contents
   instead of dropping messages.
