# cmcoder Phase 2, through LangChain / LangGraph eyes

This guide grows as Phase 2 is built. Read the
[Phase 1 LangGraph guide](../phase1/langgraph-guide.md) first.

Phase 2 puts the agent behind a **front end in another language** (a VS Code
extension in TypeScript). In the LangChain world this is what **LangGraph
Server / the LangGraph SDK** (`useStream`, `client.runs.stream`) and the
**Agent Chat UI** do. Each section follows the same pattern: **the LangGraph
way**, **the cmcoder way**, **why they differ**, **exercise**. Status of each
section follows [PLAN.md](PLAN.md).

## Preview of the mapping

| Phase 2 item | LangChain / LangGraph equivalent |
|---|---|
| 1. `--protocol stdio` | LangGraph Server's run streaming + `Command(resume=...)` for interrupts |
| 2. Generated TS types | The typed `@langchain/langgraph-sdk` client |
| 3–4. Extension and chat panel | Agent Chat UI, `useStream()` in React |
| 5–6. Permissions and diff review | Rendering an `interrupt()` payload; approve/edit/reject tool calls |
| 7–8. Editor context and IDE tools | Passing context in the run input; client-side tools |
| 9. Sessions | Threads (`client.threads.search`, `thread_id`) |

---

## 1. `cmcoder --protocol stdio`

*Done.* Code: `src/cmcoder/cli/stdio.py`, `src/cmcoder/protocol/messages.py`.

### The LangGraph way

You deploy the graph with **LangGraph Server** (HTTP) and a client streams a
run. Human approval is an `interrupt()` inside a node; the client resumes
the run with `Command(resume=...)`:

```python
# in the graph
def call_tool(state):
    answer = interrupt({"tool": "Edit", "args": {...}})   # pauses the run
    if not answer["allow"]:
        return {"messages": [ToolMessage("denied", tool_call_id=...)]}
    ...

# in the client
async for chunk in client.runs.stream(thread_id, "agent", input={"messages": [...]},
                                      stream_mode=["messages", "updates"]):
    ...  # the interrupt shows up as an "__interrupt__" update
await client.runs.create(thread_id, "agent", command={"resume": {"allow": True}})
```

### The cmcoder way

| LangGraph | cmcoder |
|---|---|
| HTTP server, one per deployment | A child process, one per VS Code window, over stdin/stdout |
| `runs.stream(..., stream_mode=...)` | One event per line on stdout (`assistant_delta`, `tool_use`, …) |
| `interrupt(payload)` pauses the run | `ask()` emits `permission_request` and awaits an `asyncio.Future` |
| `Command(resume=value)` | `{"type": "permission_response", "request_id": …, "allow": …}` |
| cancel a run | `{"type": "interrupt"}` cancels the turn's asyncio task |
| thread state in the checkpointer | the conversation in the agent object, saved to the session file |

### Why they differ

- **No server to run.** Each VS Code window starts its own `cmcoder`
  process, which works offline, needs no port and runs as the user. That
  matters for file access and for the company network rules.
- **The run doesn't stop at an interrupt.** In LangGraph the run *ends* at
  `interrupt()` and a new run resumes it later (state is saved in between).
  In cmcoder the turn is still running, just waiting on a future, so
  nothing needs saving or restoring. The trade-off: if the process dies
  during a prompt, the turn is lost (the conversation before it is saved).
- **Typed both ways.** The client's messages are pydantic models too, so a
  bad message gets an `error` event instead of crashing the agent.

### Exercise

Write a 20-line Python client: start `cmcoder --protocol stdio` with
`asyncio.create_subprocess_exec`, send a `user_message`, print each
`assistant_delta`, and answer every `permission_request` with `allow: false`
and `feedback: "read-only today"`. Compare the code with a LangGraph SDK
client that resumes interrupts.
