# Phase 5 — Code search (RAG): status

**Status:** ✅ complete (5 October 2026). **Snapshot:** the code as of the
commit that adds critique (`41e4ca1`) and its two CI fixes, recorded with
this file's last update.

Phase 5's goal ([PLAN.md](PLAN.md)): *the model can find code by meaning in
large repositories, not only by exact words, using an index of the project in
a vector database.* Met, in the terminal and in VS Code, with both gateways.

## Delivered

All 6 items in [PLAN.md](PLAN.md), on the decisions of 4 October (both
gateways, both stores, a tool and automatic context, no existing company RAG):

1. **Embeddings through the gateway**: one OpenAI-format call for LiteLLM
   (`/v1/embeddings`) and Open WebUI (`/api/embeddings`, which serves Ollama
   models too); batching, retries, the gateway's TLS, proxy and key handling;
   Open WebUI's bare 500 for an unknown model explained; `doctor` checks it.
2. **The indexer**: chunks along code structure (Python's parser, definitions
   in other languages, Markdown headings), secrets, generated, binary and
   linked-out files never indexed, incremental by fingerprint, saved batch by
   batch, refreshed after the agent's own edits and under stale results.
3. **Vector stores**: a built-in local store (SQLite + numpy, in the
   standalone build), Chroma on the machine (`chroma` extra), and a shared
   Chroma server over its REST API (key in the keychain, read-only mode for an
   index CI keeps current, collection named from the git remote).
4. **`CodeSearch` tool and automatic context**: read-only like Grep; the best
   matches added to each message within a budget (at most 8% of the window),
   each piece once per conversation; never fatal; shown in every front end.
5. **Commands, guided setup, front ends**: `cmcoder rag setup|status|on|off`,
   `cmcoder index [--status|--rebuild|--clear]`, `/index`; the same setup steps
   from VS Code (Set Up Code Search, status bar); a project's `rag.store`
   needs trust.
6. **Tests, evals, security review, docs**: tests for every part (all three
   stores, both real gateways, protocol, CLI, standalone build), the
   `code-search` eval task with `--rag on|off`, the retrieval harness
   (`evals/retrieval.py`), [SECURITY-REVIEW.md](SECURITY-REVIEW.md), the guides
   and [TESTING.md](TESTING.md).

## Features added from use (5 October)

Part of Phase 5, though not code search: they came from using cmcoder on a
.NET solution in VS Code during the phase's hands-on check.

- **Subagents**: a subagent at its step limit writes its report instead of
  stopping mid-sentence; `subagentMaxTurns` (default 100) apart from
  `maxTurns`; Task calls in one reply **run in parallel** (`maxParallelSubagents`,
  default 4; `parallelToolCalls` on for Qwen3 models), permission questions
  one at a time.
- **The agent map** (Claude Code style) in the terminal, the TUI and VS Code:
  each subagent's state, steps, tools, tokens, time and current activity;
  **stop one subagent** without stopping the turn; `/agents`. Protocol:
  `subagent_status`, `stop_subagent`.
- **The agent navigator** (Claude Code style mind map) in every front end:
  the turn's main agent → its subagents (state, steps, tools, tokens, time) →
  their last tool calls, live; a subagent's steps and report, and Stop, from
  the map. VS Code: an editor tab ("Open Agent Navigator", or Map in the
  Subagents box). Terminal: under the spinner, `/agents map` afterwards. TUI:
  above the input, and a navigator screen (Ctrl+G: a tree, details, S to
  stop).
- **Critique** (decided and built 5 October): with `/critic on` (or
  `--critic`, `critic.enabled`) a read-only critic agent reviews the turn's
  final answer against the files and the turn's diff before it is shown; on
  failure its findings go back to the agent ("the reviewer found 1 problem;
  fixing it"), up to `critic.maxRounds` (2), after which the answer is shown
  "not validated". It uses the main model and shows in the agent map.
- **VS Code progress line**: an animated ✻ in the theme's blue with what the
  agent is doing, time, tokens and "Esc to interrupt"; messages typed while it
  works are **queued** and sent when the turn ends.
- **Symbols in the classic Windows console**: it showed `?` for the spinner,
  ● … ☐ ✓; there cmcoder now uses symbols from the console's code page and
  turns the rest into ASCII (`symbols` setting, `doctor` says which).
- `cmcoder mcp approve` on your own server explains that it needs no approval.

## Validation

| Where | Result |
|---|---|
| CI (Linux, macOS, Windows; 11 jobs) | 625 Python tests, 21 extension tests, real-VS Code test, 25 mock eval tasks, mock evals with `-p`/VS Code parity, real LiteLLM and Open WebUI jobs (embeddings included), a real Chroma 1.5 server, security job (Bandit, pip-audit with the extras, npm audit) |
| Security review ([SECURITY-REVIEW.md](SECURITY-REVIEW.md)) | Index folders made private (0700); chromadb's server advisories (no fix yet) named in CI, with how to run a Chroma server safely |
| Your Windows machine, real gateway (Qwen3.5-35B-A3B, Qwen3.6-27B) | Phase 5 marked complete by you on 5 October after using it: VS Code on a .NET solution (subagents fanned out per project), the CLI in the classic console, an MCP server over your own codebase index (`codebase_code`). The fixes above came from that use |

## Changed from the plan

- **One embeddings call for both gateways**: reading Open WebUI's source
  showed its `/api/embeddings` already routes Ollama models, so no separate
  Ollama path was needed.
- **Chroma server without the `chromadb` client**: its REST API through
  cmcoder's own HTTP client (company CA, proxy, ~110 MB less), so it works in
  the standalone build.
- **Guided setup** (`cmcoder rag setup` and the VS Code flow) added on 4
  October so nothing needs a settings file edited by hand.
- The subagent, agent map and navigator, critique, progress line and console
  items above were not in the plan; they came from use and were kept as Phase
  5 features (decided 5 October).

## Carried forward

- **Retrieval quality with your embedding model**: `uv run python
  evals/retrieval.py` (hit@1, hit@5, MRR) hasn't been run with a real model
  (CI uses deterministic mock vectors as a harness check), nor the eval
  comparison `evals/run.py --rag on|off` on a real model.
- **Chroma server advisories**: no fixed chromadb release yet; the decision
  between keeping Chroma behind an authenticating proxy (documented) or adding
  pgvector as the shared store is open.

## Guides

- [code-search.md](code-search.md): set up and use code search.
- [python-guide.md](python-guide.md): the Phase 5 code for a Python developer.
- [langgraph-guide.md](langgraph-guide.md): RAG in LangChain/LangGraph terms, mapped to cmcoder.
- [TESTING.md](TESTING.md): the hands-on checklist (parts A–G).
