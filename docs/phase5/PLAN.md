# Phase 5 — Code search (RAG): plan

**Status:** ✅ complete (5 October 2026); see [STATUS.md](STATUS.md).

**Goal:** the model can find code **by meaning** ("where do we refresh the
auth token?") in large repositories, not only by exact words (Grep), using an
index of the project in a vector database (Chroma or another).

**Done when:** a developer runs `cmcoder index` (or it happens automatically),
the model uses semantic code search in the CLI and VS Code, the index stays
current as files change, nothing secret is indexed, and the evals show fewer
turns and tokens on a large repository with no loss in pass rate.

## Why

Grep and Glob find exact text; the model has to guess the words. On a large
repository with a small context window (32k Qwen or Ollama models) that costs
turns and context. An index of the code, turned into embeddings (vectors that
capture meaning) by an embedding model on the gateway, lets the model ask for
"code that does X" and get the right functions back directly.

## Decisions (4 October 2026)

| Question | Decision |
|---|---|
| Embedding model | **Both gateways**: LiteLLM's OpenAI-compatible `/v1/embeddings` and Open WebUI (its OpenAI-compatible models, and Ollama embedding models such as `nomic-embed-text`). The same settings work with either; `doctor` says which is in use. No local (gateway-less) embeddings. |
| Vector store | **Both**: a per-developer local index (built in, no heavy dependencies) and **Chroma**, on the machine or a **shared Chroma server** for a team. Qdrant/pgvector later behind the same interface, if asked. |
| How the model uses it | **Both**: a **`CodeSearch` tool** the model calls (like Grep) **and automatic context** (the best matches added to each message within a token budget). Automatic context is on when an index exists; it can be turned off or its budget changed. |
| An existing company RAG | **None.** Only the built-in index; connecting another RAG through MCP stays possible (Phase 3) but isn't part of this phase. |

## Items, in order

### 1. Embeddings through the gateway

- `providers`: an `embed(texts)` call on the provider interface, working with
  **both gateways**: OpenAI-compatible `/v1/embeddings` (LiteLLM, and
  OpenAI-compatible backends behind Open WebUI), and Ollama's embed endpoint
  through Open WebUI for Ollama models. Tested against both (mock server and
  the real LiteLLM and Open WebUI CI jobs).
- Setting `rag.embeddingModel` (a model ID, provider-prefixed like the chat
  models); batching, retries and the same TLS, proxy and key handling as chat.
- `cmcoder doctor`: the embedding model answers, its vector size.
- Mock server: `/v1/embeddings` (deterministic vectors) and Open WebUI's form.
- Status (4 Oct): done. Open WebUI 0.11 has an OpenAI-compatible
  `/api/embeddings` that sends Ollama models to Ollama's `/api/embed` itself,
  so one call serves both gateways (`<base>/embeddings`). Checked against a
  real LiteLLM proxy and a real Open WebUI (an Ollama model and an
  OpenAI-compatible one); Open WebUI's bare 500 for an unknown model is
  explained. `CMCODER_EMBEDDING_MODEL`, `CMCODER_RAG`; numpy added (vectors).

### 2. The indexer

- Files: the project's source, following `.gitignore`, plus `rag.include` /
  `rag.exclude`. **Never** what cmcoder treats as secret (`.env`, keys,
  `secrets/`, the Read tool's rules), nor binary, generated or very large files.
- Pieces ("chunks") along code structure: functions and classes (Python's
  own parser for `.py`; simple rules for other languages), with a small overlap
  and a size limit; each keeps its path, line range, language and symbol name.
- Incremental: a fingerprint per file; only changed files are re-embedded.
  Updated at session start (files changed since last time) and after the
  agent's own edits (Write/Edit); deleted files removed.
- Progress and cancellation; works the same on Windows.
- Status (4 Oct): done (`rag/chunker.py`, `rag/files.py`, `rag/index.py`).
  Python split with its parser (a large class: method by method), other
  languages at definitions, Markdown at headings; small neighbours joined.
  Generated, minified, lock and binary files skipped as well as secrets and
  links out of the project. Saved batch by batch; edits by the agent and
  files changed under a search result are refreshed before results are
  returned.

### 3. Vector stores

- One interface (add, delete by file, search top-k with a filter, stats).
- **`local`** (default): built in, SQLite with the vectors and a fast cosine
  search; no new heavy dependency, so it works in the standalone build.
  Typical repositories (up to about 100k pieces).
- **`chroma`**: Chroma on the machine (`pip install cmcoder[chroma]`), or a
  **Chroma server** by URL (HTTP client only, lighter). A shared server holds
  code: TLS and an API key, kept like the gateway key (keychain or environment,
  never in a file). Server addresses only in settings, never in code.
- Index location: `~/.cmcoder/index/<project>/` for local stores, never inside
  the project. One collection per project and embedding model (changing the
  model rebuilds).
- Status (4 Oct): done (`rag/stores.py`). A Chroma server is used through
  its REST API (v2) with cmcoder's own HTTP client, so it needs no extra
  package and works in the standalone build; Chroma on this machine is the
  `chroma` extra. A shared index is named from the git remote, so every clone
  finds the same collection; `readOnly` for an index CI keeps current.
  Tested on all three (a real Chroma 1.5 server in CI).

### 4. Using it: `CodeSearch` and automatic context

- **`CodeSearch(query, path?, limit?)`** tool: returns the best-matching
  pieces (path, lines, symbol, text) within a size limit. Read-only, no
  permission prompt, like Grep. Available only when an index exists; the
  model's prompt says when to prefer it over Grep (meaning vs exact text).
- **Automatic context** (`rag.autoContext`, on when an index exists): before
  each user message, the best matches within `maxTokens` are added as
  context, marked as retrieved code (path and lines), skipping pieces already
  in the conversation; counted in compaction. Its budget is capped by the
  model's context window (a small share of a 32k window).
- Subagents (the `explore` agent) get `CodeSearch` too.
- Status (4 Oct): done. `CodeSearch` (read-only, no prompt; results with
  file, lines, names and line numbers) and automatic context (at most
  `maxTokens` and 8% of the window; matches under `minScore` left out; not for
  messages under 3 words or slash commands; each piece sent once per
  conversation, again after compaction). Shown as "Added code from the
  index: …" in the terminal, TUI, `-p --verbose` and VS Code (new protocol
  event `code_context`). A failing index never stops a turn (one warning).
  The index is brought up to date in the background when a session starts;
  `/rewind` gives back the message without the added code.

### 5. Commands, front ends and settings

- `cmcoder index` (build or update), `--status`, `--clear`; `/index` in the
  REPL, TUI and VS Code; `doctor` reports the index (files, pieces, age).
- VS Code: indexing progress in the panel, an "Index this project" command,
  search results shown in the tool card with links to the files.
- Settings (sketch):

  ```json
  "rag": {
    "enabled": "auto",
    "embeddingModel": "corp:text-embedding-3-small",
    "store": { "type": "local" },
    "include": ["src/**"],
    "exclude": ["**/vendor/**"],
    "autoContext": { "enabled": true, "topK": 5, "maxTokens": 2000 }
  }
  ```

- A repository's `rag` settings can't point indexing at an outside server
  without project trust (as with telemetry); managed settings can require a
  company server or forbid one.
- Guided setup (added 4 Oct, so it can all be done without editing a file):
  `cmcoder rag setup` (or the same steps from VS Code): pick the embedding
  model from the gateway's list (checked with a test request), where the
  index lives (this machine, Chroma here, a Chroma server: checked; its key
  to the keychain), whose settings (yours or the project's), and index now.
- Status (4 Oct): done. `cmcoder index [--status|--clear|--rebuild]`,
  `cmcoder rag setup|status|on|off`, `/index [status]` in the terminal, TUI
  and VS Code panel (building an index turns code search on for the running
  session), doctor's index and store checks. VS Code: a status bar item
  (files indexed, a spinner while indexing; click: update, rebuild, show,
  set up, delete) and "Set Up Code Search" / "Update Code Index" commands,
  over new protocol messages (`index`, `rag_candidates`, `rag_setup`) and
  events (`index_status`, `index_progress`, `rag_candidates`,
  `rag_setup_result`).

### 6. Tests, evals, security review and docs

- Unit tests: chunking, secret files skipped, incremental updates, each
  store; Chroma against a real Chroma in CI.
- Retrieval quality: question → expected file/function pairs on a medium
  repository; how often the right piece is in the top 5.
- Evals: new tasks on a larger repository, with and without the index
  (turns, tokens, pass rate), through `-p` and the VS Code protocol.
- Security review as in earlier phases (what leaves the machine, secrets,
  shared server), plus the standalone build and the VSIX with the new code.
- Guides: Python and LangGraph sections (LangChain's retrievers and vector
  stores mapped to cmcoder's), a setup page (embedding model on LiteLLM and
  on Open WebUI, local index, Chroma server), hands-on checklist on Windows,
  `STATUS.md`.
- Status (4 Oct): done except the hands-on check and `STATUS.md`. Tests for
  every part (all three stores, both gateways for real, the protocol, the
  CLI, the standalone build); `evals/tasks/code-search` and `--rag off`;
  `evals/retrieval.py` (hit@1/hit@5/MRR with your embedding model; CI runs it
  with the mock as a harness check, since the real number needs a real
  model). [SECURITY-REVIEW.md](SECURITY-REVIEW.md): index folders made
  private; chromadb advisories (no fix yet) with how to run a Chroma server
  safely. Guides: [code-search.md](code-search.md),
  [python-guide.md](python-guide.md), [langgraph-guide.md](langgraph-guide.md);
  checklist: [TESTING.md](TESTING.md).

## Found in use (5 October 2026)

Asked in VS Code to "fan out subagents, one per project", a `general-purpose`
subagent used all 50 model calls reading controllers and was stopped; the main
agent got only its unfinished last sentence. Fixed:

- **A report at the limit.** A subagent is told when 5 calls are left; at the
  limit it makes one last call without tools and writes its report, marked as
  possibly incomplete.
- **Its own limit:** `subagentMaxTurns` (default 100), separate from `maxTurns`.
- **In parallel:** Task calls in one reply run at the same time, up to
  `maxParallelSubagents` (default 4); permission questions come one at a time
  (an "always allow" answer covers a waiting question); denying one stops the
  others; terminal lines are tagged with the task. Models send several calls
  per reply only with `parallelToolCalls`, now on for Qwen3 models.
- **The agent map** (asked for the same day, Claude Code style), in the
  terminal, the TUI and VS Code: a live tree of the turn's subagents (state,
  steps of its limit, tools, tokens, time, current activity), stopping one
  subagent without stopping the turn (it reports what it has), and `/agents`
  (agent types, this session's runs, a run's steps and report). Protocol:
  `subagent_status` events, `stop_subagent` message.
- **The agent navigator** (asked for the same day): the turn as a mind map,
  main agent → subagents → their last tool calls, with details and Stop. A
  VS Code editor tab; under the spinner in the terminal (`/agents map`); in
  the TUI above the input and as a navigator screen (Ctrl+G).
- **VS Code progress line** with queued messages, and **symbols the classic
  Windows console can draw** (code-page aware, `symbols` setting).

## Feature: critique (planned and built 5 October 2026)

A **critic agent** checks the answer before it reaches the user; only an
answer that passes is shown. If it fails, the main agent gets the critic's
findings, fixes the work, and the critic checks again.

| Question | Decision (5 Oct) |
|---|---|
| When it runs | **Only when switched on**: `/critic on|off`, `--critic`, setting `critic.enabled` (default off) |
| What it reviews | **The final (summary) answer** of the turn, not each subagent's report |
| Its model | **The main model** |
| Still failing after the retries | **Show the answer, marked "not validated"**, with the critic's remaining findings |
| While the agent fixes | **The findings are shown** ("the reviewer found 2 problems; fixing them") |
| Build/test commands as checks | **No**: the critic model only |

How: the final reply is held; a read-only `critic` subagent (Read, Glob,
Grep, CodeSearch) gets the request, the draft, what the agent did and this
turn's diff (from the checkpoints, secrets left out), and ends with a
`Verdict` tool call (pass/fail, issues). Pass: the answer is shown. Fail: the
findings go back to the main agent as a reminder and the loop continues, up to
`critic.maxRounds` (default 2). The critic shows in the agent map and
navigator like any subagent; its prompt can be replaced with a `critic.md`
agent file. Protocol: a `review_result` event; `result.review`.

Status (5 Oct): built. `core/critic.py`, the gate in `Agent._run`,
`/critic` in the terminal, TUI and VS Code panel, `--critic`,
`CMCODER_CRITIC`, VS Code setting `cmcoder.critique`. Tests:
`tests/test_critic.py`; eval `evals/tasks/critic-review` (a half-done first
answer the critic fails; 25/25 tasks pass with `-p`/VS Code parity).

## Not in Phase 5

Indexing documents other than code (wikis, PDFs: an existing company RAG
through MCP covers that), cross-repository search, re-ranking models, graph
or call-tree indexes. Later, if asked.

## Checklist

- [x] Decisions confirmed (4 Oct: both gateways, both stores, tool and automatic context, no existing RAG)
- [x] 1. Embeddings through the gateway
- [x] 2. The indexer
- [x] 3. Vector stores (local, Chroma)
- [x] 4. `CodeSearch` and automatic context
- [x] 5. Commands, guided setup, front ends and settings
- [x] 6. Tests, evals, security review and docs
- [x] Hands-on use on Windows, and [`STATUS.md`](STATUS.md) (5 Oct)
