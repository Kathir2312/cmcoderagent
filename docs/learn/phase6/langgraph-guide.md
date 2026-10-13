# cmcoder Phase 6, for LangChain / LangGraph developers

Phase 6 puts cmcoder into IDEs. That is a front end, and LangGraph has no
counterpart for it.

How LangGraph apps are usually put into an IDE, and how cmcoder compares:

| Usual LangGraph approach | cmcoder |
|---|---|
| A LangGraph Server (HTTP + streaming) and an IDE plugin calling it | cmcoder runs **locally** per project; the plugin starts it and talks JSON lines over stdin/stdout (`--protocol stdio`), with no port and no server to secure |
| `astream_events` / `stream_mode="updates"` to show progress | cmcoder's protocol events (`assistant_delta`, `tool_use`, `permission_request`, `subagent_status`, …) are the stream; the shared chat page renders them |
| `interrupt()` + `Command(resume=…)` for human approval | a `permission_request` event; the IDE's diff viewer or the page answers with `permission_response` (allow, always, deny with feedback) |
| A checkpointer for resuming threads | cmcoder's saved sessions; picking one in the page's History restarts cmcoder with `--resume=<id>` |
| A tool that needs the host (IDE) | `ide_capabilities` at start; the model calls `getDiagnostics` / `openFile`, cmcoder asks the IDE (`ide_tool_request`), and the plugin answers |

The idea carries over to a LangGraph app: one UI (a web page) rendered in
each host's browser, plus a small host layer per IDE, instead of one native
UI per IDE.
