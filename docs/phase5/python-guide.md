# cmcoder Phase 5, explained for Python developers

Code search (RAG): the model finds code by meaning. This guide follows the
[plan](PLAN.md) item by item; setup for users is in
[code-search.md](code-search.md).

---

## 1. Embeddings through the gateway

*Code:* `providers/openai_compat.py` (`embed`), `providers/openwebui.py`,
`rag/embed.py`. *Tests:* `tests/test_rag_embed.py`, plus the real LiteLLM and
Open WebUI tests.

### The idea

An **embedding** is a list of numbers (a vector) for a piece of text, made by
an embedding model so that texts with similar meaning get vectors pointing in
similar directions. Comparing two pieces of text then becomes comparing two
vectors: the **cosine similarity** (1 = same direction, 0 = unrelated).

### The code

- `OpenAICompatProvider.embed(model, texts)` posts `{"model", "input": [...]}`
  to `<base>/embeddings` and reads `data[i].embedding`, sorted by `index`.
  The same call works for LiteLLM (`/v1/embeddings`) and Open WebUI
  (`/api/embeddings`): reading Open WebUI's source showed that endpoint
  already routes Ollama models to Ollama's `/api/embed` and converts the
  answer. One subtlety: Open WebUI answers a **bare 500** for an unknown
  model, so `OpenWebUIProvider.embed` checks the model list and explains.
- `Embedder` batches 32 texts per request, trims very long ones, and returns
  a **numpy** `float32` matrix with **unit-length rows**. Unit length matters:
  the cosine of two unit vectors is just their dot product, so search becomes
  one matrix multiplication.

### New Python ideas

- **numpy broadcasting:** `matrix / np.linalg.norm(matrix, axis=1, keepdims=True)`
  normalises every row at once; `keepdims` keeps the shape `(n, 1)` so the
  division lines up row by row.
- A deterministic stand-in model for tests (`testing/mock_server.py:
  fake_embedding`): words hashed into signed buckets, so texts sharing words
  are similar. Good enough to test plumbing; useless for quality, which is
  why `evals/retrieval.py` exists.

## 2. The indexer

*Code:* `rag/chunker.py`, `rag/files.py`, `rag/index.py`. *Tests:*
`tests/test_rag_index.py`.

### Chunking

A whole file is too big to embed usefully; a line is too small. cmcoder cuts
files into **chunks** along their structure:

- Python: `ast.parse` gives each top-level function and class with
  `lineno`/`end_lineno`; a large class is split method by method (each method
  keeps the blank lines and comments before it). Decorators are included
  (`min(node.lineno, *decorator linenos)`).
- Other languages: a regular expression finds unindented definition lines
  (`function`, `class`, `def`, `func`, `fn`, `interface`, ...); Markdown splits
  at headings.
- Then small neighbours are merged (`_merge_small`) and big spans are cut into
  windows of at most 80 lines / 2,400 characters with a 5-line overlap
  (`_limit`), so nothing falls between two chunks.

What's embedded is `embedding_text()`: the path and the names first, then the
code. The path and names carry a lot of meaning ("billing/invoice.py",
"compute_vat").

### Which files

`FileSelector` reuses `walk_files` (Grep's own walk: hidden folders,
`node_modules` and `.gitignore` honoured), then refuses secrets with
`safe_project_file` (the Read tool's rules, resolving links), generated and
minified files, binaries (a NUL byte in the first 8 KB) and anything too big.

### Staying current

The **manifest** (`manifest.json` next to the index) maps each file to
`(mtime_ns, size, sha1)`. `_changes()` compares cheaply first (time and
size), and only reads and hashes files whose time changed, so a `git
checkout` that touches times but not contents costs no embeddings. Updates
go 20 files at a time, saving the manifest after each batch: interrupting an
update loses at most one batch.

Freshness during a session has three layers:

1. The agent's own `Write`/`Edit` call `note_changed(path)`; dirty files are
   re-embedded before the next search.
2. `start_background_update()` brings the index up to date when a session
   starts, as an `asyncio.Task` that `close()` cancels.
3. `search()` checks the files of its own results (`_stale`): a file changed
   by someone else is re-embedded and the search runs again.

## 3. Vector stores

*Code:* `rag/stores.py`. *Tests:* the parametrised index tests run on all
three.

One small `Protocol` (`add`, `delete_paths`, `search`, `count`, `clear`,
`close`), three implementations:

- **`LocalStore`:** SQLite holds chunks and vectors (`np.ndarray.tobytes()`
  into a BLOB); the first search loads them into one matrix, and a search is
  `matrix @ query` then `np.argsort`. A write clears the cached matrix.
  Nothing to install, so it works in the standalone build.
- **`ChromaServerStore`:** Chroma's REST API (v2) with cmcoder's own `httpx`
  client: `POST .../collections` (`get_or_create`, cosine space), `upsert`,
  `delete` with `{"path": {"$in": [...]}}`, `query` with `n_results`. Using
  the REST API instead of the `chromadb` client keeps ~110 MB of dependencies
  out and reuses the company CA, proxy and TLS handling. Chroma returns
  cosine *distance*: score = 1 - distance.
- **`ChromaLocalStore`:** the `chromadb` library in-process (the optional
  `chroma` extra); its blocking calls run in `asyncio.to_thread`.

A shared server's collection is named from the git remote (`origin`), so
every clone of the repository finds the same index.

## 4. Using it: CodeSearch and automatic context

*Code:* `tools/code_search.py`, `core/agent.py` (`_auto_context`),
`cli/factory.py`. *Tests:* `tests/test_rag_agent.py`.

- **`CodeSearchTool`** is a read-only `Tool` like Grep: the permission engine
  lets it run without a prompt inside the project. Results show the location,
  names, score and numbered lines.
- **Automatic context** runs in `Agent._run` after the hooks: search with the
  user's words, keep matches above `minScore` within a token budget
  (`min(maxTokens, 8% of the window)`), skip pieces already sent in this
  conversation (`_context_sent`, reset by compaction and `/clear`), and put
  them in a `<system-reminder>` ahead of the message. The `code_context` event
  tells the front ends. `visible_text()` strips reminders, so `/rewind` gives
  back what the user typed.
- **Never fatal:** any exception from the index during automatic context sets
  `_context_failed`, yields one warning, and the turn goes on.

## 5. Commands, setup and front ends

*Code:* `cli/rag_cmd.py`, `rag/setup.py`, `cli/stdio.py`,
`vscode/src/codeSearch.ts`. *Tests:* `tests/test_rag_cli.py`,
`tests/test_rag_stdio.py`.

- `rag/setup.py` holds the setup steps as plain async functions
  (`embedding_candidates`, `check_embedding_model`, `check_store`,
  `apply_setup`). The terminal wizard (`typer.prompt`, `rich` status) and the
  VS Code flow (protocol messages `rag_candidates` and `rag_setup`) call the
  same functions: one place to fix, the same settings written.
- `index_command()` in `factory.py` is `/index` for all three front ends;
  when it builds the first index of a session it **attaches** it
  (`attach_code_index`: the tool, automatic context and the prompt's note).
- The stdio server runs indexing and setup as **side tasks**
  (`_side`), so the conversation keeps working, with `index_progress` events
  for VS Code's status bar; they're cancelled at shutdown.

## 6. Evals and security

- `evals/tasks/code-search`: the code to fix is named differently from the
  request ("value added tax" vs `apply_levy`), the case code search is for.
  `evals/run.py --rag off` runs the same tasks without it.
- `evals/retrieval.py`: 20 questions about cmcoder's own code with the
  function that answers each; hit@1, hit@5 and MRR for your embedding model.
- [SECURITY-REVIEW.md](SECURITY-REVIEW.md): index folders made private
  (0700), chromadb advisories and how to run a Chroma server safely.

## 7. Critique: a critic agent before the answer is shown

*Code:* `core/critic.py` (`CRITIC_PROMPT`, `VerdictTool`, `turn_diff`,
`review_prompt`, `review`, `critic_command`), the gate in `Agent._run`,
`TaskTool.run_agent` (`core/subagents.py`), settings `critic`. *Tests:*
`tests/test_critic.py`; the eval `evals/tasks/critic-review`.

### The problem

A model sometimes says "done" when half the work is done, or describes code
it didn't change. The user finds out later. A second look before the answer
is shown catches much of that.

### The idea

With critique on (the VS Code checkbox, `/critic on`, `--critic`, `critic.enabled`), the turn's
final reply is **held**. A read-only **critic** subagent gets the request,
the draft, what the agent did and the turn's diff; it checks them against the
files and calls `Verdict(pass|fail, summary, issues)`. Pass: the answer is
shown. Fail: the issues go back to the main agent as a reminder and the loop
continues, up to `critic.maxRounds`; after that the answer is shown marked
"not validated".

### The code

- **Holding the reply.** In `_run`, `hold = self.critique and not
  self.is_subagent`: streamed text goes into `held` instead of out as
  `assistant_delta`. When the step turns out to be a tool step, `held` is
  released at once; when it's the final answer (`deferred`), it waits for the
  review.
- **The diff** comes from the checkpoints (`/rewind` already keeps each
  file's state before the turn's first edit): `difflib.unified_diff` of that
  and the file now, project files only, secret files left out (`is_secret`).
- **The verdict is a tool call**, not JSON in text: models format tool
  arguments far more reliably, and pydantic (`VerdictInput`) validates them.
  The tool instance keeps the result for `review()` to read.
- **The critic is an ordinary subagent**: `TaskTool.run_agent(definition,
  ..., extra_tools=[verdict])` gives it a run record, `subagent_status`
  events and a place in the agent map and navigator, for free. `critic.md`
  (your agents folder) replaces its prompt; its tools stay read-only.
- **Never lose the answer**: a critic that errors or gives no verdict yields
  `Review("none")`, and the answer is shown "not reviewed".
- **One checkbox for every window** (`SavedCritique`): the checkbox saves
  `critic.enabled` in `~/.cmcoder/settings.json`. Each running cmcoder
  remembers the file's `(mtime, size)` and, when it changes, reads the value:
  the VS Code server every 2 seconds while idle (an `asyncio` task beside the
  conversation), every front end at the start of a turn. Only a *change*
  counts, so a `--critic` given at startup isn't overridden by an old value;
  its own write updates the stamp, so it isn't news to itself. A file it can't
  parse is never overwritten.

### New Python ideas

- **Evaluator–optimizer loop** inside an `async` generator: `continue` sends
  the agent back to work; the generator decides what the user sees and when.
- **A tool as a typed return channel**: the model "returns" structured data by
  calling a tool whose input model is the data's schema.

### Try it

```
cmcoder --critic        # or /critic on in a session
```
