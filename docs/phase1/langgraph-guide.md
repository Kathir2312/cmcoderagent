# cmcoder Phase 1, through LangChain / LangGraph eyes

This guide grows as Phase 1 is built. Read the
[Phase 0 LangGraph guide](../phase0/langgraph-guide.md) first: it maps the
core agent loop, tools, permissions and streaming to LangGraph.

Phase 1 adds the features that LangGraph users usually get from a
**checkpointer**, **memory management** and **human-in-the-loop** tooling.
Each section follows the same pattern:

1. **The LangGraph way**: how you'd build the feature with LangChain/LangGraph,
   with a short code sketch.
2. **The cmcoder way**: how cmcoder builds it, with links to the code.
3. **Why they differ**: what cmcoder needs that the framework version doesn't cover.
4. **Exercise**: a small task that connects the two.

Status of each section follows [PLAN.md](PLAN.md).

## Preview of the mapping

| Phase 1 item | LangChain / LangGraph equivalent |
|---|---|
| 1. Auto-compaction | A summarise node / `pre_model_hook`, `ConversationSummaryMemory`, `trim_messages` |
| 2. Permission prompt | How you render an `interrupt()` payload to a person |
| 3. Context window detection | Model profiles (`ChatModel.profile`), token counting (`get_num_tokens_from_messages`) |
| 4. Bash for file work | Tool descriptions, `tool_choice`, prompt engineering, LangSmith-style evals |
| 5. Managed settings | No direct equivalent; closest is server-side config in LangGraph Platform |
| 6. Sessions and resume | Checkpointer (`SqliteSaver`, `PostgresSaver`) + `thread_id` |
| 7. Checkpoints and `/rewind` | Time travel: `get_state_history()`, replay from a checkpoint, `update_state()` |
| 8. TodoWrite | A `todos` key in graph state, updated by a tool via `Command(update=...)` |
| 9. Small/fast model jobs | Several chat models in one graph, e.g. a cheap model in a summarise node |
| 10. Tool-call robustness | `AIMessage.invalid_tool_calls`, output parsers, `with_retry`, `with_fallbacks` |
| 11. Textual TUI | Consuming `graph.astream(..., stream_mode=[...])` in a UI |
| 12. More evals | LangSmith datasets and evaluators, `agentevals` trajectory scoring |

---

## 1. Auto-compaction

*Not started.*

## 2. Permission prompt never scrolls off screen

*Done.* Code: `src/cmcoder/cli/repl.py`.

### The LangGraph way

LangGraph stops at `interrupt(payload)` and hands the **payload** to
whatever is driving the graph. Showing it to a person is your job:

```python
out = graph.invoke(inputs, config)
if "__interrupt__" in out:
    req = out["__interrupt__"][0].value      # e.g. {"command": "...", "reason": "..."}
    print(req["command"])                    # a 500-line command floods the screen here
    answer = input("approve? [y/n] ")
    out = graph.invoke(Command(resume=answer), config)
```

The framework stops at "here is the payload". How long the payload is, and
whether the question is still on screen when the person has to answer it, is
up to the UI.

### The cmcoder way

The agent side is unchanged: `Agent._run_call` still sends a
`PermissionRequest` (cmcoder's interrupt payload) to `ask()`. Only the UI
that renders it changed:

- the preview is clipped to the terminal height (`clip_preview`), keeping the
  first and last lines;
- the options are printed after the preview and repeated in the input line;
- the "always allow" rule is shown as one shortened line (`short_rule`);
- `v` shows the full payload on request, and invalid answers re-show the options.

### Why they differ

This is the same lesson in both worlds: **human-in-the-loop is only as safe
as its UI.** If a person can't see what they're approving, or can't find the
options, they'll press `1` to make the prompt go away. A good approval
screen:
- keeps the start and the end of the payload visible, which is where heredoc
  targets and closing `EOF`s are;
- states the risk (`high-risk: …`);
- makes the full text available on demand.

### Exercise

In the LangGraph sketch from the
[Phase 0 guide](../phase0/langgraph-guide.md#5-cmcoder-written-in-langgraph-sketch),
make the model run a 300-line heredoc and print the interrupt payload. Then
write a `render_interrupt(payload, height)` function that does what
`clip_preview` does, and check it with `shutil.get_terminal_size()`.

## 3. Detecting the real context window

*Not started.*

## 4. Steering the model away from Bash for file work

*Not started.*

## 5. Managed settings

*Not started.*

## 6. Sessions and resume

*Not started.*

## 7. Checkpoints and `/rewind`

*Not started.*

## 8. TodoWrite

*Not started.*

## 9. Small/fast model jobs

*Not started.*

## 10. Tool-call robustness

*Not started.*

## 11. Textual TUI

*Not started.*

## 12. More evals

*Not started.*
