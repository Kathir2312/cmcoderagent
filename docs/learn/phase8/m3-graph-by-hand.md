# M3: the loop by hand, as a LangGraph `StateGraph`

Before M4 builds the engine with LangChain's `create_agent`, this step
builds the same loop by hand from cmcoder's real parts, the M1 chat model
and the M2 tools, so you see what `create_agent` does for you
([plan](../../project/phases/phase8/PLAN.md)).

**You learn:** state and reducers; nodes and edges; conditional edges;
compiling a graph; streaming modes; the recursion limit; a checkpointer and
threads; drawing a graph; LangGraph Studio.

**Lab:** [labs/m3_graph.py](labs/m3_graph.py), against the mock gateway:

```
uv run python docs/learn/phase8/labs/m3_graph.py
```

## 1. The graph

```
START ──► model ──(tool calls?)──► tools ──┐
            ▲  └──(no, or too many steps)──► END
            └───────────────────────────────┘
```

The lab prints the same graph as Mermaid (`app.get_graph().draw_mermaid()`).

```python
graph = StateGraph(State, context_schema=EngineContext)
graph.add_node("model", call_model)        # calls the chat model with the tools bound
graph.add_node("tools", ToolNode(tools))    # runs the tool calls of the last AIMessage
graph.add_edge(START, "model")
graph.add_conditional_edges("model", route, ["tools", END])
graph.add_edge("tools", "model")
app = graph.compile(checkpointer=InMemorySaver())
```

## 2. State and reducers

The state is a typed dict that every node reads and updates. A node returns
only what it changes, and each key's **reducer** says how an update is
combined with what's there:

| Key | Reducer | What an update does |
|---|---|---|
| `messages` (from `MessagesState`) | `add_messages` | appends (and replaces a message with the same id) |
| `steps: int` | none | replaces the value |
| `files_read: Annotated[list[str], operator.add]` | `operator.add` | appends the list |

The lab's step 3 shows the result after a turn: `steps` is the last count,
`files_read` holds every file the model asked to read.

## 3. Nodes and edges

- A **node** is a function (sync or async) from the state to an update:
  `call_model` returns `{"messages": [reply], "steps": n + 1, "files_read": [...]}`.
- An **edge** says what runs next; a **conditional edge** calls a function
  on the state to choose. `route` is what LangGraph's `tools_condition` does
  (tools if the last message has tool calls, otherwise the end), plus
  cmcoder's own limit on model calls (`maxTurns`).
- `START` and `END` are the graph's entry and exit.

## 4. Running it: invoke and stream

| Call | Gives |
|---|---|
| `await app.ainvoke(input, config, context=...)` | the final state |
| `app.astream(..., stream_mode="updates")` | each node's update, as it finishes (step 2) |
| `app.astream(..., stream_mode="messages")` | the model's tokens as they arrive, with the node they come from (step 4) |
| `stream_mode="values"` | the whole state after each step |
| `stream_mode="custom"` | what nodes send with `get_stream_writer()` (M4: tool progress) |

M4's event bridge turns these streams into cmcoder's protocol events for the
IDEs.

## 5. Stopping

`config={"recursion_limit": N}` caps the number of steps (each node run
counts); reaching it raises `GraphRecursionError` (step 5). cmcoder's
`maxTurns` counts model calls instead, and stops cleanly in `route`, so the
turn ends with an answer rather than an exception.

## 6. Memory: the checkpointer and threads

Compiled with a checkpointer, the graph saves its state after every step
under the run's `thread_id` (`config={"configurable": {"thread_id": ...}}`).
The next run in the same thread starts from the saved state: the lab's
second question sees the first turn (step 4). `await app.aget_state(config)`
reads it back; M5 builds sessions, resume and rewind on this, with SQLite.

## 7. LangGraph Studio

Studio is LangSmith's web UI for a graph running on your PC: you see the
graph, step through a run, and inspect and edit the state at each step. It
needs a free LangSmith account; with `LANGSMITH_TRACING=false`, nothing about
your runs leaves the PC (only the browser talks to your local server).

[labs/studio/](labs/studio/) has the entry point. The graph there gets
**read-only tools** (Read, Glob, Grep), because a graph built by hand has no
permission checks, and it reads the gateway from your shell, never from a
file. In PowerShell, from the cmcoder repository:

```
$env:CMCODER_BASE_URL    = "https://<your gateway>/v1"
$env:CMCODER_API_KEY     = "<your key>"
$env:CMCODER_MODEL       = "<your model>"
$env:CMCODER_LAB_PROJECT = "C:\path\to\a\project"    # what the tools may read
$env:LANGSMITH_TRACING   = "false"
cd docs\learn\phase8\labs\studio
uv run --project ..\..\..\..\.. --with "langgraph-cli[inmem]" langgraph dev
```

`langgraph dev` prints a Studio link; sign in to LangSmith there. (Running
it found one bug, now fixed: the server makes the runtime context with no
arguments when a run gives none, so `EngineContext.tool_ctx` is optional.)

## 8. What `create_agent` adds (M4)

`create_agent(model, tools, middleware=[...])` builds this graph for you:
the model node, the tool node, the conditional edge. What's left is
cmcoder's behaviour, and LangChain's place for it is **middleware**: code
that runs before and after the model, around each model call and around
each tool call. M4 puts cmcoder's permissions, hooks, context management and
the empty-reply retry there.

## Exercises

1. Add a key `tools_run: Annotated[int, operator.add]` to `State` and have a
   node count tool calls into it. Where must the update come from?
2. Replace `route` with LangGraph's own `tools_condition` (from
   `langgraph.prebuilt`). What does the graph lose?
3. Run step 2 with `stream_mode=["updates", "messages"]`: what does each
   item look like now?
4. In step 4, use a different `thread_id` for the second question: what does
   the model see?
5. Print `[s.next for s in app.get_state_history(config)]` after step 4 (with
   the sync API, or `aget_state_history`): what does each saved step say
   comes next? (M5's `/rewind` uses this.)
