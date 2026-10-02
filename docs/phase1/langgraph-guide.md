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

*Not started.*

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
