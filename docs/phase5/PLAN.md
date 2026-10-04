# Phase 5 — Code search (RAG): plan

**Status:** planned (4 October 2026); starts when Phase 4 is complete.

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

## Two ways to connect a RAG

| | What it is | In this phase |
|---|---|---|
| **Built-in index** | cmcoder indexes the project itself and keeps it current | Items 1–6 |
| **A RAG service the company already runs** | Connected as an **MCP server** (supported since Phase 3) | A guide only; nothing to build |

## Decisions

Recommended answers, **to be confirmed** before the phase starts:

| Question | Recommendation | Status |
|---|---|---|
| Embedding model | The gateway's: LiteLLM's OpenAI-compatible `/v1/embeddings` (e.g. `text-embedding-3-small`, `bge-m3`), or Open WebUI's Ollama models (e.g. `nomic-embed-text`). Local embeddings (no gateway) only if neither is available: about +100 MB to the install. | To confirm: which embedding models the gateway has |
| Vector store | **Per-developer local index** by default (built in, no heavy dependencies); **Chroma** as an option, on the machine or a **shared Chroma server** for a team. Qdrant/pgvector later behind the same interface, if asked. | To confirm |
| How the model uses it | A **`CodeSearch` tool** the model calls when it helps (like Grep); **automatic context** (top results added to each message within a token budget) as an option, off by default. | To confirm |
| An existing company RAG | Connect it through MCP (guide). | To confirm whether one exists |

## Items, in order

### 1. Embeddings through the gateway

- `providers`: an `embed(texts)` call on the provider interface: OpenAI-compatible
  `/v1/embeddings` (LiteLLM, and OpenAI-compatible backends behind Open WebUI),
  Ollama's embed endpoint through Open WebUI for Ollama models.
- Setting `rag.embeddingModel` (a model ID, provider-prefixed like the chat
  models); batching, retries and the same TLS, proxy and key handling as chat.
- `cmcoder doctor`: the embedding model answers, its vector size.
- Mock server: `/v1/embeddings` (deterministic vectors) and Open WebUI's form.

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
- **Automatic context** (`rag.autoContext`, off by default): before each
  user message, the top results within `maxTokens` are added as context,
  marked as retrieved code; counted in compaction.
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
    "autoContext": { "enabled": false, "topK": 5, "maxTokens": 2000 }
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
  stores mapped to cmcoder's), a setup page (embedding model, Chroma server,
  MCP for an existing RAG), hands-on checklist on Windows, `STATUS.md`.

## Not in Phase 5

Indexing documents other than code (wikis, PDFs: an existing company RAG
through MCP covers that), cross-repository search, re-ranking models, graph
or call-tree indexes. Later, if asked.

## Checklist

- [ ] Decisions confirmed (embedding model, store, retrieval mode, existing RAG)
- [ ] 1. Embeddings through the gateway
- [ ] 2. The indexer
- [ ] 3. Vector stores (local, Chroma)
- [ ] 4. `CodeSearch` and automatic context
- [ ] 5. Commands, front ends and settings
- [ ] 6. Tests, evals, security review and docs
- [ ] Hands-on use on Windows, and `STATUS.md`
