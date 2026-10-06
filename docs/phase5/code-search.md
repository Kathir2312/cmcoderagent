# Code search: setup and use

Code search gives the model an **index of the project's code**, so it can find
code by what it does ("where is the VAT computed?"), not only by exact words.
It works with both gateways (LiteLLM and Open WebUI), in the terminal and in
VS Code.

- **`CodeSearch` tool:** the model calls it like Grep, describing what the
  code does.
- **Automatic context:** before each of your messages, the best-matching
  pieces of code are added (within a token budget). You see a line like
  "◦ Added code from the index: money/charges.py:1-12 (≈180 tokens)".

## 1. What you need

An **embedding model** on your gateway (it turns code into vectors):

| Gateway | Typical embedding models | Check |
|---|---|---|
| LiteLLM | whatever the admin added with `mode: embedding`, e.g. `text-embedding-3-small`, `bge-m3`, `nomic-embed-text` | `cmcoder models` |
| Open WebUI | an Ollama embedding model (`ollama pull nomic-embed-text` on the Ollama server) or an OpenAI-compatible one behind Open WebUI | `cmcoder models --provider <name>` |

cmcoder calls `<gateway>/embeddings` in the OpenAI format: LiteLLM's
`/v1/embeddings`, or Open WebUI's `/api/embeddings` (which passes Ollama
models on to Ollama itself). Nothing else to configure on Open WebUI.

## 2. Set it up

**Terminal** (in the project):

```
cmcoder rag setup
```

It asks four things:

1. **Embedding model:** a list from your gateways (embedding-looking names
   first). The one you pick is checked with a test request.
2. **Where the index lives:**
   - *On this machine, built in*: nothing to install. The default.
   - *Chroma, by its URL*: a Chroma running on this PC
     (`http://localhost:8000`) or a server a team shares. Its URL and API key
     (the key goes to your OS keychain, never a file), and whether you only
     search it (someone else, e.g. CI, keeps it up to date). Chroma is always
     used through its URL for now: Chroma inside cmcoder isn't offered (the
     standalone program can't include it).
3. **Whose settings:** yours (`~/.cmcoder/settings.json`) or the project's
   (`.cmcoder/settings.json`, shared through git).
4. **Index now?**

Without questions (scripts, CI):

```
cmcoder rag setup -m corp:bge-m3 --store local --scope user --index --yes
cmcoder rag setup -m corp:bge-m3 --store chroma-server --url https://chroma.corp:8000 --read-only --yes
cmcoder rag setup -m corp:bge-m3 --store chroma-server --url http://localhost:8000 --yes
```

**Chroma on this PC** without Python: Docker
(`docker run -d -p 127.0.0.1:8000:8000 -v chroma-data:/data chromadb/chroma`), then
the URL `http://localhost:8000`. See [../guides/code-search.md](../guides/code-search.md).

**VS Code:** Command Palette → **"cmcoder: Set Up Code Search"** (or click
"Code search: off" in the status bar). **Eclipse / Visual Studio:** the code
search item above the chat. The same four steps, in the IDE's dialogs. All
write the same settings.

## 3. Use it

| Where | What |
|---|---|
| Any session | The model uses `CodeSearch` when it helps; automatic context adds code to your messages |
| `cmcoder index` | Bring the index up to date (changed files only); `--status`, `--rebuild`, `--clear` |
| `/index`, `/index status` | The same inside a session (terminal, TUI, VS Code panel). Building an index turns code search on for the running session |
| VS Code status bar | "Code search: 1,240 files"; a spinner while indexing; click for update / rebuild / show / set up / delete |
| `cmcoder rag status` / `on` / `off` | See the setup, or turn it off and on (the index is kept) |
| `cmcoder doctor` | Checks the embedding model, the store and this project's index |

The index stays current by itself: changed files when a session starts, the
agent's own edits before the next search, and any file that changed under a
search result before the result is returned.

## 4. Settings

```json
"rag": {
  "enabled": "auto",
  "embeddingModel": "corp:bge-m3",
  "store": { "type": "local" },
  "include": [],
  "exclude": ["**/vendor/**", "docs/**"],
  "maxFileBytes": 512000,
  "autoUpdate": true,
  "autoContext": { "enabled": true, "topK": 5, "maxTokens": 2000, "minScore": 0.3 }
}
```

| Key | Meaning |
|---|---|
| `enabled` | `"auto"`: on when an embedding model is set and the project has an index. `false`: off. `true`: on, and says so if the project has no index |
| `embeddingModel` | `provider:model` (or a model of the default provider) |
| `store` | `{"type": "local"}`; `{"type": "chroma", "url": "...", "readOnly": true, "collection": "..."}` (Chroma at its URL, on this PC or a server; `url` is required; `headers` may use `${VAR}`) |
| `include` / `exclude` | gitignore-style patterns. Secret files (`.env`, keys, `secrets/`) are never indexed, whatever these say |
| `autoContext` | `topK` pieces at most, `maxTokens` at most (and never more than 8% of the model's window), only matches scoring `minScore` or more |
| `autoUpdate` | Update changed files when a session starts |

Environment: `CMCODER_EMBEDDING_MODEL`, `CMCODER_RAG=on|off`,
`CMCODER_CHROMA_API_KEY`.

A project's `.cmcoder/settings.json` may set everything except `store` (where
the code goes) unless the project is trusted (`cmcoder trust`). Managed
settings can require a company server or turn code search off.

## 5. A shared Chroma server for a team

One index for everyone on a repository, kept current by CI:

1. **Deploy Chroma securely.** Read [SECURITY-REVIEW.md](SECURITY-REVIEW.md)
   finding 2 first: Chroma servers currently have unpatched advisories. Keep
   it on the internal network, behind an authenticating TLS proxy, one
   instance per team.
2. **CI** (on the default branch) keeps it current:
   ```
   cmcoder rag setup -m corp:bge-m3 --store chroma-server --url https://chroma.corp:8000 --scope user --yes
   CMCODER_CHROMA_API_KEY=$CHROMA_WRITE_KEY cmcoder index
   ```
3. **Developers** search it read-only:
   ```
   cmcoder rag setup -m corp:bge-m3 --store chroma-server --url https://chroma.corp:8000 --read-only
   ```
   (or VS Code: "Only search it").

Every clone finds the same collection: its name comes from the git remote
(`cmcoder-<repo>-<hash>`) and the embedding model; `store.collection` sets
it explicitly. Everyone must use the **same embedding model** as the writer.

## 6. Measuring it

```
uv run python evals/retrieval.py                 # your embedding model
uv run python evals/run.py --rag on              # tasks with code search…
uv run python evals/run.py --rag off             # …and without
```

`retrieval.py` asks 20 questions about cmcoder's own code and reports how
often the right function is first (hit@1) or in the top 5 (hit@5).

## Troubleshooting

| Symptom | Fix |
|---|---|
| "doesn't work as an embedding model" | It's a chat model, or the gateway doesn't have it: `cmcoder models` |
| Open WebUI: "has no model …" | The name as Open WebUI lists it (`cmcoder models --provider <name>`); for Ollama, `ollama pull` it on the server |
| "Can't reach the Chroma server" | Address, VPN, proxy (`HTTPS_PROXY`/`NO_PROXY`), company CA (`rag.store.caCertPath`) |
| "refused the request (401/403)" | The key: `cmcoder rag setup` again, or `CMCODER_CHROMA_API_KEY` |
| "Code search isn't answering" during a session | The turn went on without it; `cmcoder doctor` |
| Results seem stale | `cmcoder index` (or `--rebuild` after changing the embedding model) |
