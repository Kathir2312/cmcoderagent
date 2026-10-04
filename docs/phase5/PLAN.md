# Phase 5 — Code search (RAG): plan

**Status:** in progress (started 4 October 2026, with Phase 4's WSL2 check still to do).

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

## Not in Phase 5

Indexing documents other than code (wikis, PDFs: an existing company RAG
through MCP covers that), cross-repository search, re-ranking models, graph
or call-tree indexes. Later, if asked.

## Checklist

- [x] Decisions confirmed (4 Oct: both gateways, both stores, tool and automatic context, no existing RAG)
- [x] 1. Embeddings through the gateway
- [ ] 2. The indexer
- [ ] 3. Vector stores (local, Chroma)
- [ ] 4. `CodeSearch` and automatic context
- [ ] 5. Commands, front ends and settings
- [ ] 6. Tests, evals, security review and docs
- [ ] Hands-on use on Windows, and `STATUS.md`
