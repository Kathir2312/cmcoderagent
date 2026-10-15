# Phase 8 — M0 spike: results

**Status (15 October 2026):** experiments E1–E6 ran in the cloud
environment against cmcoder's mock gateway, which checks the history the way
vLLM does. Still to run on your side: **E7** against your real gateway (Qwen3.6
on vLLM), **E6 on Windows**, and **LangGraph Studio** (`langgraph dev`, which
needs a free LangSmith account). Then design decision D18 and the go or
no-go.

**Recommendation: go.** Nothing found blocks the plan; every finding has a
known fix, now written into the design rules below.

## How to run them

From the repository root, with uv (no other set-up; the stack is pinned in
[spike/requirements.txt](spike/requirements.txt)):

```
uv run --no-project --with-requirements docs/project/phases/phase8/spike/requirements.txt `
    python docs/project/phases/phase8/spike/e1_chat_openai.py
```

Each script prints `PASS`, `FAIL` (a check that failed) or `NOTE` (what the
library does). They start the mock gateway themselves, except E4's program and
E7 (see their files). E1's `c2` and `d` fail on purpose: they show LangChain's
own behaviour, which the new engine's chat-model subclass and middleware must
fix; in M1 the same checks become tests that pass.

## Versions tested

langchain 1.4.6, langchain-core 1.6.10, langchain-openai 1.7.2, langgraph
1.2.15, langgraph-checkpoint-sqlite 3.1.2, langchain-mcp-adapters 0.3.2,
langsmith 0.14.9, langfuse 5.0.1, langmem 0.0.30, openevals 0.2.0, agentevals
0.0.9, langgraph-supervisor 0.0.31, langgraph-swarm 0.1.0, PyInstaller 6.22.3,
Python 3.12. The whole stack is 96 packages, 133 MB installed.

## Results

| # | Experiment | Result | What it means for the design |
|---|---|---|---|
| E1a | `ChatOpenAI` in `create_agent`: a tool round trip | **Pass** | The mock gateway (and so cmcoder's test suite) works unchanged with LangChain |
| E1b | Qwen's reasoning (`reasoning_content`) | **Dropped** by `ChatOpenAI` | `CmcoderChatOpenAI` must read the reasoning field (`reasoning_content`, or `reasoning` in newer vLLM) |
| E1c | A tool call with broken JSON, then one more message | The call becomes an *invalid tool call*; the turn **ends without telling the model**; the next message **fails with 400 "Expecting ',' delimiter"**, the same error cmcoder hit on 14 October | The subclass sends past arguments as strict JSON; a middleware turns invalid calls into error results the model sees |
| E1d | An empty reply, then one more message | The empty reply is **kept and sent back** | The empty-reply middleware (retry once, leave it out) is needed |
| E2 | `interrupt()` with two tool calls in one reply (one needs approval), resumed with `Command(resume=…)`, in-memory and SQLite | **Pass**: each tool ran exactly once | Approvals through `interrupt` are safe for tools |
| E2 | Middleware code before `interrupt()` | **Runs again** on resume (twice for the call that needed approval) | Rule: a PreToolUse hook's result is kept per tool call id, so a hook never runs twice |
| E3 | Network, with every lookup outside this PC refused | **Pass**: nothing but the gateway, including summarising (its default token counter is approximate, so tiktoken never downloads) | — |
| E3 | `LANGSMITH_TRACING=true` set in the developer's shell | LangChain **sends every turn to api.smith.langchain.com by itself** | Rule: cmcoder clears the tracing variables first thing at start-up |
| E3 | The variables cleared only after LangChain was imported | **Still sends**: LangChain has read them already | The guard must run before any LangChain import, and a test proves it |
| E4 | A PyInstaller program with the stack, run with no Python on PATH | **Pass**: one agent turn with summarising and SQLite checkpoints | Packaging works |
| E4 | Size | 97 MB (an empty program: 19 MB; today's cmcoder: 120 MB) | cmcoder estimated at **about 180 MB**; measured for real in M1 |
| E4 | Start-up | Imports 2.1–2.3 s; a whole one-turn run 3.2–3.4 s; today's cmcoder 1.2–1.4 s for the same request | **About 2 s more** per start (once per chat in the IDEs, every run with `-p`); lazy imports in M1, with a limit in CI |
| E4 | Summarising at 80% of the window | Refused: LangChain doesn't know Qwen's window | cmcoder sets the model profile's `max_input_tokens` from its own window detection |
| E5 | `langchain-mcp-adapters` and cmcoder's `mcp` | The adapters need **`mcp` below 2.0**; cmcoder uses **2.3 or newer** | A decision (below) |
| E5 | A real stdio MCP server | Default: **a new server process for every call** (3 for 3 calls); with `client.session()` none (pass) | Lasting sessions only |
| E5 | Tool names, results, logs, TLS | Names `tickets_find_ticket` (cmcoder: `mcp__tickets__find_ticket`); results as content blocks; no option to send a server's error output to its own log; `httpx_client_factory` for TLS | Rename; flatten results; keep per-server logs ourselves |
| E6 | SQLite checkpoints: 25 steps with 20 kB outputs | About **1 ms more per step** (Linux; Windows to run) | Fast enough |
| E6 | Database size | **7.2 MB** for one such turn: every step stores the whole history, so size grows with the square of a turn's length | Rule: keep only the checkpoints at the start of each user message (what `/rewind` needs) and prune the rest |
| — | `langmem`'s dependencies | Pulls in `langchain-anthropic`, which cmcoder doesn't use | Accept (small) or leave it out of the build in M10 |

## Design rules from the spike

1. `CmcoderChatOpenAI` reads the reasoning field, sends past tool arguments as
   strict JSON and never sends an empty reply back.
2. Invalid tool calls become error results the model sees.
3. A PreToolUse hook runs once per tool call id, also across an approval.
4. The tracing guard runs before any LangChain import.
5. cmcoder fills the model profile (`max_input_tokens`) from its window
   detection.
6. MCP servers use lasting sessions; tools keep cmcoder's names; results are
   flattened to text; per-server logs stay.
7. Checkpoints are pruned to the start of each user message.
8. Start-up time and program size have limits in CI.

## Decisions this raises (for D18)

| Question | Options | Recommendation |
|---|---|---|
| MCP | (a) keep cmcoder's MCP client on `mcp` 2.x and wrap its tools as LangChain `StructuredTool`s; (b) move to `mcp` 1.x to use `langchain-mcp-adapters` | **(a)** until the adapters support `mcp` 2.x; the adapters stay in the learning labs |
| Start-up (+2 s) and size (about 180 MB) | accept, with limits in CI | Accept, and cut what lazy imports can in M1 |

## For you to run

1. **E7** against your gateway: set `CMCODER_BASE_URL`, `CMCODER_API_KEY` and
   `CMCODER_MODEL` and run [spike/e7_real_gateway.py](spike/e7_real_gateway.py)
   (instructions in the file). Send back its `PASS`/`FAIL`/`NOTE` lines.
2. **E6 on Windows**: run [spike/e6_sqlite_speed.py](spike/e6_sqlite_speed.py)
   the same way and send back its two `NOTE` lines.
3. **Studio**: `uv run --no-project --with "langgraph-cli[inmem]" langgraph dev`
   in a folder with a small agent (M3's lab will provide one); keep
   `LANGSMITH_TRACING=false` so nothing leaves the PC.
