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

*Done.* Code: `src/cmcoder/core/compaction.py`, `Agent.compact` and the
checks in `Agent.run` (`core/agent.py`).

### The LangGraph way

LangGraph's how-to on managing conversation history suggests a
**summarise node** that runs when the message list gets long. It writes a
summary into state and deletes the old messages with `RemoveMessage`:

```python
from langchain_core.messages import HumanMessage, RemoveMessage
from langgraph.graph import MessagesState


class State(MessagesState):
    summary: str


def summarize(state: State):
    old = state["messages"][:-4]                       # keep the last 4 messages
    prompt = f"Summary so far: {state.get('summary', '')}\nExtend it with the conversation above."
    summary = small_llm.invoke([*old, HumanMessage(prompt)]).content
    return {"summary": summary, "messages": [RemoveMessage(id=m.id) for m in old]}


def should_summarize(state: State):
    return "summarize" if count_tokens(state["messages"]) > 0.8 * WINDOW else "agent"
```

The other common tools are `trim_messages` (drop old messages) and, in
`create_react_agent`, a `pre_model_hook` that rewrites the messages before each
model call. The `langmem` library also ships a `SummarizationNode`.

### The cmcoder way

The same idea, done inside the agent loop:

| LangGraph | cmcoder |
|---|---|
| `should_summarize` conditional edge | `if budget.estimate(...) >= window * compact_threshold` in `Agent.run` |
| `summarize` node | `Agent.compact()` → `compaction.compact()` |
| `state["summary"]` | a user message starting with `SUMMARY_HEADER` |
| `RemoveMessage(id=…)` for old messages | `self.messages = [system, summary, *tail]` (one swap) |
| "keep the last 4 messages" | keep the newest ~25% of the window, cut at a step boundary |
| a cheaper `small_llm` | `smallFastModel`, with the main model as fallback |

### Why they differ

The naive "keep the last N messages" in the sketch above has a bug that bites
tool-using agents. If message `-4` is a `ToolMessage`, its `AIMessage` with
the tool call gets removed and the next model call fails ("tool result
without a tool call"). LangChain's `trim_messages` has `start_on="human"` /
`end_on=(...)` options for exactly this. cmcoder's `split_index` only cuts at
user/assistant boundaries.

cmcoder also handles things the sketch doesn't:

- **The summariser's own window.** The text to summarise can be bigger than
  the small model's window, so it is summarised in chunks with a running
  summary.
- **The user's request, word for word.** It's copied verbatim, not left to
  the model, and carried over across repeated compactions.
- **Prompt injection.** The summary goes back as a *user* message, so the
  summariser is told that file contents and tool output are data and must
  never be written as user requests. The `create_react_agent` +
  `pre_model_hook` approach needs the same care.
- **Failure.** If summarising fails, it falls back to the next model, then to
  dropping old output. It's never retried before every step.
- **Wrong estimates.** A server "context too long" error triggers compaction
  and a retry, and cmcoder stops trusting the estimate that failed.

### Exercise

Add the `summarize` node above to the
[Phase 0 LangGraph sketch](../phase0/langgraph-guide.md#5-cmcoder-written-in-langgraph-sketch),
using your gateway's Qwen3-7B as `small_llm`. Make it fail by keeping the
last 3 messages when message `-3` is a `ToolMessage`, then fix it the way
`split_index` does.

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

*Done.* Code: `providers/openai_compat.py`, `cli/factory.py`, the
`ContextTooLong` handler in `core/agent.py`.

### The LangGraph way

LangChain needs to know the window too, for `trim_messages(max_tokens=…)` and
for your "summarise at 80%" condition. Typical options:

```python
llm = ChatOpenAI(base_url=GATEWAY, model="Qwen3.6-27B")

WINDOW = 32768                                    # 1. hard-code it
WINDOW = llm.profile.get("max_input_tokens")      # 2. LangChain 1.x model profiles
                                                  #    (from a public model database;
                                                  #     a private Qwen deployment isn't in it)
used = llm.get_num_tokens_from_messages(msgs)     # counting needs a tokenizer
```

When the guess is wrong, you get an `openai.BadRequestError` from deep inside
`llm.invoke`, and the graph run fails. Recovering is up to you, e.g. with
`.with_retry()` or a fallback, but the limit stated in the error is not
used.

### The cmcoder way

| Question | cmcoder |
|---|---|
| What's the window? | settings → what the server enforces (probe / error) → `/model/info` → built-in |
| How do we find out? | a deliberate over-long request (`max_tokens=10,000,000`) that the server rejects, stating its limit |
| What if it's wrong mid-run? | the `ContextTooLong` error carries the stated limit; the agent switches to it, saves it, compacts, retries |
| How are tokens counted? | characters ÷ a ratio calibrated against the server's reported usage (no tokenizer download) |

### Why they differ

A framework sits on top of many providers and can't assume what their errors
say. cmcoder targets OpenAI-compatible servers (vLLM behind LiteLLM), whose
"maximum context length is N" message is reliable enough to use as a
**source of truth**. It's tested against vLLM, LiteLLM, llama.cpp and TGI
wordings, and through a real LiteLLM proxy.

The general lesson for any agent framework: **errors carry information.**
Classify them, extract what they tell you, and feed it back into state.
That beats only retrying.

### Exercise

With `ChatOpenAI` pointed at your gateway, call
`llm.invoke("hi", max_tokens=10_000_000)` inside `try/except
openai.BadRequestError as e:` and print `e.message`. Write a
`learn_window(e)` that pulls out the number with
`cmcoder.providers.openai_compat.parse_context_window`, and use it as the
`max_tokens` for `trim_messages` in your graph.

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
