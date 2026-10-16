# Phase 8 — the engine on LangChain and LangGraph: plan

**Status:** M1 under way (15 October 2026). M0, the spike, ended in a go
([SPIKE.md](SPIKE.md), design decision D18); E7 on the real gateway, E6 on
Windows and Studio still run on the user's side.

**Goal:** rewrite cmcoder's core engine on the Python agent stack (LangChain,
LangGraph, LangSmith, Langfuse and the libraries around them), so that the
team learns that stack on a real product. Developers see no difference: the
same commands, settings, permission behaviour, IDE plugins and chat page.

**Done when:** the new engine is the only engine, every front end (terminal,
TUI, `-p`, all five IDE plugins) runs on it unchanged, the comparison runs
(below) match the old engine on every scripted scenario, the release builds
pass on Windows, macOS and Linux within the size and startup limits, the
security review is signed off, and `docs/learn/phase8/` explains every part
with a lab.

> **Note: developers' PCs may not have Python installed** (nor uv, pip or
> Node.js). cmcoder stays one PyInstaller program with the new libraries
> inside; see [DESIGN.md](../../DESIGN.md) decision D16.

## Decisions (15 October 2026)

| # | Question | Decision |
|---|---|---|
| 1 | How the main loop is built | **`create_agent` + cmcoder middleware**, after a hand-built `StateGraph` lab (M3) that shows what `create_agent` does underneath. Explicit `StateGraph`s for the team, the critic and agentic code search. |
| 2 | Tracing targets (LangSmith cloud or self-hosted, Langfuse self-hosted) | **Decided before M8** (the user's call). Until then all tracing is off; M0 and M3 use LangSmith only with test projects. |
| 3 | Long-term memory (LangGraph Store + `langmem`) | **Included**, as an optional feature, in M5. |
| 4 | The team workflow | **Hand-built supervisor graph first**, then compared with `langgraph-supervisor` and `langgraph-swarm`. |
| 5 | Prompt management in LangSmith or Langfuse | **Decided with 2**: syncing prompts sends cmcoder's prompt texts (no code, no keys) to the same services. Until then prompts are files in the repository, which stay the fallback. |
| 6 | The old engine | **Removed in M11**, with the tag `classic-engine` kept to compare against. |
| 7 | People | **Three developers**, about 3–4 months, split (M1, M2, M7), (M3–M6), (M8–M10). One developer would take about 6–7 months. |
| 8 | MCP (from M0; revisited 16 Oct) | **`langchain.mcp`**, LangChain's own MCP support (in `langchain` 1.4.6, in beta), on FastMCP 4's client and the `mcp` library 2.x; cmcoder's MCP client uses the same FastMCP client, so both engines share one connection per server. (On 15 Oct it was `langchain-mcp-adapters`, which needs `mcp` 1.x; we had missed `langchain.mcp`. See [SPIKE.md](SPIKE.md), E5b.) |
| 9 | Size and start-up (from M0) | **Accepted**: about 180 MB and about 2 s more at start-up, with limits in CI. |

## What changes and what doesn't

| | What | Why |
|---|---|---|
| **Rewritten** | The loop (`core/agent.py`), the model layer (`providers/`), tools, MCP, sessions and checkpoints, context and compaction, subagents, the critic, code search (`rag/`), titles, vision, evals, tracing | Where the libraries replace our code, and where the learning is |
| **Reused** (called from the new engine) | Permission rules (`permissions.py`, `risk.py`, `readonly.py`), the hook runner, the sandbox, settings, `transport.py` and `auth.py` (TLS, keychain), the tool bodies (Read, Edit, Bash, …) | No library has an equivalent; rewriting them teaches nothing about agents and would risk Claude Code parity and safety |
| **Unchanged** | `cli/` front ends (rewired only), the Agent Protocol, the chat page, the five IDE plugins, the packaging scripts | The IDEs keep working; the comparison runs prove it |

Requirements that stay as they are: no Python on developers' PCs (D16);
LiteLLM and Open WebUI (D12, D15) with Qwen on vLLM (D10); TLS through the OS
certificate store, never switched off (D14); no server names in the code; API
keys only in the keychain, including any LangSmith or Langfuse key; Claude
Code parity (tool names, permission modes, hook events, slash commands,
`CMCODER.md`, resume, rewind); the Agent Protocol (D7); Windows, macOS and
Linux; the sandbox; images; Chroma by URL (D17).

Tracing and evaluation can send code to a cloud service. That is allowed only
when switched on for a test project; releases ship with all tracing off and a
managed setting can forbid it (decision 2).

## How it fits together

```
front ends (unchanged): repl · tui · headless · stdio ──► IDE plugins
        │  Agent Protocol events
        ▼
engine/events.py     graph.astream(stream_mode=["messages","updates","custom"], subgraphs=True) → events
        ▼
engine/graph.py      create_agent(model, tools, middleware=[...], checkpointer, store, context_schema)
  middleware/        prompt · auto-context (RAG) · vision · summarise · context editing · limits ·
                     empty reply · todos · hooks · permissions (interrupt) · steer · wrap-up
  agents/            Task → subagent graphs · team supervisor graph · critic reflection graph
        ▼
llm/                 CmcoderChatOpenAI(ChatOpenAI) + PromptedToolsChatModel · embeddings · profiles
tools/               StructuredTools (+ langchain.mcp for MCP) · CodeSearch retriever tool
rag/                 text splitters · OpenAIEmbeddings · Chroma / in-memory store · index() + RecordManager
persistence          AsyncSqliteSaver (sessions, rewind) · SqliteStore + langmem (long-term memory)
observability        LangSmith · Langfuse · local JSONL tracer (callbacks) · OpenTelemetry metrics
evaluation           LangSmith datasets/experiments · Langfuse datasets/scores · openevals · agentevals
```

### The libraries and what each one teaches

| Library | Used for | You learn |
|---|---|---|
| `langchain-core` | Messages, content blocks, Runnables, prompts, output parsers, callbacks, indexing | The building blocks and the Runnable interface |
| `langchain` (1.x) | `create_agent`, built-in and custom middleware, `init_chat_model` | Today's standard agent and its middleware hooks |
| `langchain-openai` | `ChatOpenAI` (subclassed for Qwen on vLLM), `OpenAIEmbeddings` | Chat models, `bind_tools`, streaming, structured output, `with_retry`, `with_fallbacks` |
| `langgraph` | `StateGraph`, reducers, `Send`, subgraphs, `interrupt` and `Command`, streaming, runtime context | Graphs, state, control flow, human in the loop, durable execution |
| `langgraph-checkpoint-sqlite` | Sessions, resume, rewind | Checkpointers, threads, time travel |
| LangGraph Store + `langmem` | Optional long-term memory: project facts and the user's preferences across sessions | Memory types, store namespaces, background memory |
| `langchain.mcp` (+ FastMCP's client) | MCP servers as tools, on lasting connections; MCP prompts as slash commands; later, a server's questions to the user through `interrupt` (elicitation) | MCP in LangChain |
| `langchain-text-splitters`, `langchain-chroma` | Code search | RAG from loading to re-indexing |
| `langgraph-supervisor`, `langgraph-swarm` | Compared with the hand-built team graph | Multi-agent patterns |
| `deepagents` | Studied and compared, not shipped (its tool names break parity) | How LangChain builds a Claude Code-style agent |
| `langgraph-cli` + LangGraph Studio | `langgraph dev`: see and step through cmcoder's graph | Visual graph debugging (Studio needs a free LangSmith account; with `LANGSMITH_TRACING=false` no data leaves the PC) |
| `langsmith` | Tracing, datasets, experiments, prompt hub | Run trees, offline evaluation, prompt versions |
| `langfuse` | Self-hosted tracing (Docker), sessions, scores, datasets, prompt management, masking | Open-source LLM observability |
| `openevals`, `agentevals` | Model-graded checks, tool-call trajectory checks | Evaluating agents, not just answers |
| `pydantic` | Structured output: critic verdicts, titles, team plans | `with_structured_output` |
| OpenTelemetry | The usage metrics cmcoder already sends | Metrics beside traces |

M0 confirms each package and pins its version.

### The engine

- **State** (`engine/state.py`): the messages plus `todos`, `turn`,
  `files_changed`, `wrap_up` and `usage`, updated through reducers.
- **Runtime context** (`context_schema`): working folder, project root,
  permission policy, hook runner, IDE capabilities, limits. Tools read it
  through the injected runtime.
- **Main agent:** `create_agent` with this middleware, in order:
  1. `@dynamic_prompt`: the system prompt with `CMCODER.md` and skills.
  2. Auto-context (before the model): code found by code search.
  3. Vision (around the model call): describes images for text-only models.
  4. `SummarizationMiddleware`: cmcoder's summary prompt, the fast model, at
     80% of the window.
  5. `ContextEditingMiddleware`: drops old tool output.
  6. Window learning: on a "too long" error, learns the real window and
     retries.
  7. `ModelCallLimitMiddleware` (`maxTurns`) and wrap-up: one last call
     without tools for a summary.
  8. Empty reply (after the model): asks once more; empty replies stay out of
     the history.
  9. `TodoListMiddleware`, with the tool still called `TodoWrite`.
  10. Hooks: SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, Stop.
  11. Permissions (around each tool call): cmcoder's rules, then
      `interrupt(payload)` with the diff.
  12. Steer: the user's messages sent while it works.
- **Qwen on vLLM** (`CmcoderChatOpenAI`): keeps the reasoning field, splits
  `<think>`, sends past tool arguments as strict JSON, leaves empty replies
  out, sizes `max_tokens`, uses cmcoder's TLS clients.
  `PromptedToolsChatModel` covers servers without a tool parser.
- **Subagents:** each `.cmcoder/agents/*.md` becomes its own agent graph,
  run by the `Task` tool; parallel runs use `Send`.
- **Critic:** a reflection `StateGraph`: answer, structured review, revise, at
  most N rounds.
- **Team:** a supervisor graph: the PM plans, the developer builds, the
  tester tests (back to the developer when tests fail), DevOps finishes. The
  user approves each stage through an `interrupt`, and the run survives a
  restart.

### Tracing and evaluation

- **Tracing**, three targets switched on one by one in settings (all off by
  default, keys in the keychain, a managed setting can forbid any): a local
  JSONL trace (a custom `BaseCallbackHandler`), LangSmith, and self-hosted
  Langfuse. Each trace carries the session id, model, engine version and
  project. Secrets are masked before sending, using cmcoder's secret-file
  patterns. The `LANGSMITH_*` and `LANGCHAIN_TRACING*` environment variables
  are cleared at startup unless tracing is allowed.
- **Evaluation:** `evals/` becomes datasets in LangSmith and Langfuse;
  experiments compare models and prompts; `openevals` grades answers with the
  gateway's model, `agentevals` checks tool-call sequences. CI stays offline
  (the mock gateway, no cloud); real-gateway evaluation runs by hand.
- **Prompts:** `ChatPromptTemplate` files in the repository (decision 5).

### Layout

```
src/cmcoder/
  engine/        graph.py · state.py · context.py · events.py · sessions.py · memory.py · middleware/
  agents/        subagents.py · team.py · critic.py
  llm/           chat_openai.py · prompted_tools.py · embeddings.py · profiles.py   (+ transport.py, auth.py kept)
  tools/         LangChain tools (bodies kept) · mcp.py (langchain.mcp)
  rag/           splitters.py · stores.py · index.py · retriever.py
  observability/ local_trace.py · langsmith.py · langfuse.py · telemetry.py
  core/          permission rules · risk · readonly · hook runner · settings glue   (reused)
  cli/ protocol/ sandbox/ config/                                                   (rewired only)
evals/           datasets/ · run_langsmith.py · run_langfuse.py · evaluators/
docs/learn/phase8/  one page per milestone, with labs/ that run on the mock gateway
```

## Milestones, in order

Each milestone builds part of the engine, teaches its concepts and adds a page
with a lab to `docs/learn/phase8/`.

| # | Build | Learn | Done when | Weeks |
|---|---|---|---|---|
| M0 | **Spike.** Pin versions. Prove: the PyInstaller program (size, startup, hidden imports); `ChatOpenAI` against the vLLM-strict mock gateway and Qwen3.6 on the real gateway (reasoning, tool calls, this week's two failures); `interrupt` running each tool exactly once; MCP adapters with TLS; SQLite speed on Windows; no network call except the gateway | The package map, Runnables | `SPIKE.md` and design decision D18; the user says go or no-go | 2 |
| M1 | `llm/`: the chat model subclass, prompted tools, profiles, embeddings, retries and fallbacks, structured output | Chat models, messages, streaming, `bind_tools`, callbacks | Unit tests on the mock gateway pass, including this week's vLLM failures | 2 |
| M2 | `tools/` as `StructuredTool`s; MCP through `langchain.mcp` | Tool schemas, runtime injection, `ToolMessage`, streamed progress | Each tool's existing tests pass through the new interface | 2 |
| M3 | Lab: the loop by hand as a `StateGraph`, seen in Studio | State, reducers, nodes, edges, `ToolNode`, `tools_condition`, `recursion_limit` | The lab in `docs/learn/phase8/labs/` | 1 |
| M4 | `engine/`: `create_agent` with middleware 1–12; the engine switch (`"engine": "classic" \| "langgraph"`); front ends rewired | Middleware hooks, `jump_to`, state and context schemas | Front ends run on the new engine; comparison runs match | 4–5 |
| M5 | Sessions and approvals: checkpointer, `interrupt` and resume, rewind through history, old sessions imported; long-term memory with Store and `langmem` | Checkpointers, interrupts, time travel, durable execution, memory | Resume, rewind and approval tests, and exactly-once tests, pass | 2–3 |
| M6 | `agents/`: subagent graphs (parallel with `Send`), the critic graph, the team supervisor (hand-built, then compared with `langgraph-supervisor` and `langgraph-swarm`), the Deep Agents study | Subgraphs, map-reduce, supervisor and swarm, reflection | The team example runs from plan to tests with the user's approvals | 3–4 |
| M7 | `rag/` on LangChain; CodeSearch v2 as a small graph (rewrite the query, retrieve, grade, answer) | RAG from start to finish, agentic RAG | Retrieval evals equal or better; Chroma by URL works | 2 |
| M8 | `observability/`: local tracer, LangSmith, Langfuse, masking, guards (decision 2 first) | Callbacks, run trees, sessions | One turn shows in each allowed tool, masked; everything off by default, tested | 1–2 |
| M9 | Evaluation: datasets, experiments, `openevals` and `agentevals`; prompt sync if decision 5 allows | Offline and online evaluation, models as judges, trajectories | An eval report in each allowed tool; CI's mock evals still green | 2 |
| M10 | Packaging, security, docs: PyInstaller hooks, OSV and licence checks, a security review (traces, checkpoints, memory, cloud), `DESIGN.md`, `docs/learn/phase8/` complete | Shipping an agent | Release builds green on all three platforms; size and startup within limits; review signed off | 2–3 |
| M11 | Rollout: a trial on the real gateway, the new engine as the default, the old engine removed (tag `classic-engine`) | — | A week of developer use with no regressions | 2 |

## How we prove nothing breaks

- **Comparison runs:** every scripted scenario runs on both engines and the
  events and requests are compared. A difference is fixed or explained in
  writing.
- **The existing tests and evals stay the gate.** The mock gateway speaks the
  OpenAI API, so `ChatOpenAI` uses it unchanged, including its vLLM-style
  checks of tool-call arguments.
- **New tests:** middleware unit tests with LangChain's fake models; each tool
  and hook runs exactly once after an approval is resumed; no key in any
  checkpoint, trace or store; tracing off unless allowed; no network call but
  the gateway (network blocked); program size and startup time.

## Risks

| Risk | What we do |
|---|---|
| `ChatOpenAI` handles Qwen on vLLM badly (lost reasoning, broken tool calls resent, empty replies) | The subclass, guarded by the tests from commits `7ebb962` and `a9295db` |
| Resuming after an approval reruns a side effect | Approval before any side effect; exactly-once tests; an in-process wait instead if M0 shows a problem |
| Company code reaching a cloud service | Tracing off by default, a managed-setting veto, the environment variables cleared, masking; decision 2 |
| A bigger, slower program; hidden imports missed by PyInstaller | Measured in M0; `collect_all` hooks; lazy imports; limits in CI |
| Library churn | Versions pinned in `uv.lock`; the 1.x LTS lines; upgrades only behind the full test gate |
| Scope creep in a learning rewrite | Every milestone ends with a working engine and green comparison runs; memory, the team and agentic code search are separate items |
| Two engines during the move | The engine switch; the old engine removed in M11 |
| Found in M0 ([SPIKE.md](SPIKE.md)): `LANGSMITH_TRACING` in a developer's shell sends every turn to the cloud; hooks rerun on resume; checkpoints grow with the square of a turn; `langchain-mcp-adapters` needs `mcp` below 2.0 (since replaced by `langchain.mcp`, decision 8); about 2 s more start-up and about 180 MB | The eight design rules in SPIKE.md; the MCP decision there |

## Checklist

- [x] Plan and decisions (15 Oct)
- [x] M0. Spike, `SPIKE.md`, D18, go (15 Oct: E1–E6 run here, results and
  design rules in [SPIKE.md](SPIKE.md); decisions 8 and 9; go. E7 on the real
  gateway, E6 on Windows and Studio run on the user's side before M1 closes.)
- [ ] M1. Model layer (15 Oct: code and tests in; closes once E7 has run on
  the real gateway. `src/cmcoder/llm/`: `CmcoderChatOpenAI` and the factory,
  prompted tool calls, the fast model's fallback, structured output,
  embeddings; the tracing guard; the learning page and lab in
  [docs/learn/phase8/](../../../learn/phase8/README.md). The engine doesn't use
  it yet, so the start-up time is measured when it does, in M4.)
- [x] M2. Tools and MCP (15 Oct: cmcoder on the `mcp` library 1.x, its MCP
  tests unchanged; `src/cmcoder/engine/`: `CmcoderTool` (the classic schema
  and error texts, the runtime context for the working folder) and MCP
  tools through `langchain-mcp-adapters` on cmcoder's lasting sessions
  (`mcp__server__tool` names, text results, error status). LangChain
  inlines `$defs` and drops `title` keys in schemas: same meaning, noted.
  The learning page and lab are in [docs/learn/phase8/](../../../learn/phase8/README.md).
  16 Oct, decision 8 revisited: MCP now goes through `langchain.mcp` on
  FastMCP 4 and the `mcp` library 2.x. cmcoder's MCP client connects
  through FastMCP's client (its tests unchanged), FastMCP is imported so a
  project's `.env` can't configure it and it doesn't log to the terminal,
  and `langchain-mcp-adapters` is gone.)
- [x] M3. Hand-built graph lab (15 Oct: [m3-graph-by-hand.md](../../../learn/phase8/m3-graph-by-hand.md)
  and its lab: the loop as a `StateGraph` from cmcoder's model and tools,
  reducers, streaming, the recursion limit, a checkpointer; a Studio entry
  point checked with `langgraph dev` here (Studio itself needs the user's
  LangSmith account). Found and fixed: a LangGraph server makes the runtime
  context with no arguments, so `EngineContext.tool_ctx` is optional. A
  test now runs every lab.)
- [ ] M4. The engine on `create_agent`, the engine switch
- [ ] M5. Sessions, approvals, long-term memory
- [ ] M6. Subagents, critic, team
- [ ] M7. Code search
- [ ] M8. Tracing (after decision 2)
- [ ] M9. Evaluation
- [ ] M10. Packaging, security, docs
- [ ] M11. Rollout, old engine removed
