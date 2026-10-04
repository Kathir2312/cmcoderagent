# Phase 5 security review: code search

**Date:** 4 October 2026. **Scope:** what Phase 5 added. That is
embeddings through the gateways (`providers/*.embed`), the index
(`rag/`: files, chunking, manifest, stores), the `CodeSearch` tool and
automatic context, the setup commands (`cli/rag_cmd.py`, `rag/setup.py`), the
VS Code protocol messages and extension code (`vscode/src/codeSearch.ts`),
and the new dependencies (numpy; chromadb as an optional extra).

**Result:** 1 issue fixed (low), 1 dependency finding documented with
mitigations (no fix exists upstream). Nothing secret is indexed, nothing is
sent anywhere new without the user choosing it, and keys stay out of files.

## How it was tested

| Kind | Tool | What it covered |
|---|---|---|
| SAST (Python) | Bandit 1.9.4 | `src/` (all rules; CI fails on medium or higher) |
| SAST | Semgrep 1.179.0, `semgrep-rules` `a84ff9c` (python, javascript, typescript, secrets, ai, bash) | the Phase 5 files and the changed ones (22 files) |
| SCA | pip-audit 2.10.1 | all 122 packages in `uv.lock` **including the optional extras** (before, CI audited without extras) |
| SCA | npm audit | the extension's packages |
| Manual | review and tests | what leaves the machine, secrets, keys, a shared server, prompt injection through the index |

## Findings

| # | Severity | Finding | Status |
|---|---|---|---|
| 1 | Low | Index folders under `~/.cmcoder/index` (copies of the code) were created with default permissions: readable by other users of a shared Linux/macOS machine | Fixed |
| 2 | — (dependency) | chromadb 1.5.9: five advisories, no fixed version (below) | Documented, mitigated |

### 1. Index folders readable by others

The built-in store and Chroma on this machine keep the code's text under
`~/.cmcoder/index/`. Session transcripts were already private (0700); the
index wasn't. **Fix:** every index folder is created owner-only (0700), up to
`~/.cmcoder/index`. Test: `test_the_index_is_private` (fails on the earlier
code).

### 2. chromadb advisories (no fix yet)

pip-audit reports, for chromadb 1.5.9 (the latest), with **no fixed version**:

| Advisory | What |
|---|---|
| CVE-2026-45829 (PYSEC-2026-311) | **Chroma server**, 1.0 and later: code execution **without authentication** through the create-collection endpoint (a malicious model repository with `trust_remote_code`) |
| CVE-2026-45833 (PYSEC-2026-3814) | Chroma server: code execution by an authenticated user with the update-collection permission (same vector) |
| CVE-2026-45831 (PYSEC-2026-3815) | `SimpleRBACAuthorizationProvider` ignores which tenant/database/collection a permission is for |
| CVE-2026-45830 (PYSEC-2026-3813) | Authenticated users can read and change any tenant's collections |

**What this means for cmcoder:**

- **The built-in store (the default)** doesn't use chromadb at all.
- **Chroma on this machine** (`cmcoder[chroma]`) runs chromadb *in-process*
  as a library: no server, no endpoint to attack. cmcoder creates only its own
  collections, with a single setting (cosine distance), never an embedding
  function or `trust_remote_code`.
- **A Chroma server** is where the risk is, and it's the server's, not
  cmcoder's: cmcoder talks to it as a client over HTTP, and its requests never
  carry an embedding-function configuration. But **anyone who can reach the
  server** may be able to run code on it (finding 1 needs no login), and its
  multi-tenant permissions don't hold.

**If you run a shared Chroma server, until Chroma publishes a fix:**

1. Don't expose it beyond the network the developers' machines are on; never
   to the internet.
2. Put an authenticating, TLS-terminating reverse proxy in front of it, and
   only allow the `/api/v2/...` routes cmcoder uses (heartbeat, list, create,
   upsert, delete, query, count).
3. One Chroma instance per team or trust boundary (don't rely on tenants).
4. Give most developers `readOnly` (`rag.store.readOnly`), with one writer
   (e.g. CI) keeping the index current.
5. Watch for the fix (the advisories above) and update the server.

CI audits chromadb now, with these five IDs named as known (so a *new*
advisory fails the build): `.github/workflows/ci.yml`, security job.

## Reviewed and unchanged

| Area | What was checked |
|---|---|
| Secrets | Never indexed: the Read tool's secret rules (`.env`, keys, `secrets/`), links that resolve outside the project or to a secret, plus binary/generated files (tests: `test_files_never_indexed`; the `.env` text never appears in results) |
| What leaves the machine | Code pieces go to the **gateway's embedding model** (the same gateway and trust as chat; TLS and company CA as for chat), and to a **Chroma server** only if the user chose one. Nothing else |
| Keys | The Chroma key goes to the OS keychain (or `CMCODER_CHROMA_API_KEY`), never to a settings file (test: `test_project_scope_never_holds_the_key`); VS Code asks for it in a password box and never logs protocol messages; protocol errors show the field name, not the value |
| A repository's settings | `rag.store` (where code goes) is ignored without project trust (test); include/exclude and automatic context are allowed (they only narrow or tune) |
| Path handling | `CodeSearch`'s `path` must be inside the project; paths in search results come from the index and are never opened unless they're in the local manifest (a shared server can't make cmcoder read or re-index files outside the project) |
| Prompt injection | Retrieved code is project content, like a file the model reads; automatic context adds it as a marked reminder, and the model is told to Read before editing. A **writable shared index** could serve text that isn't in anyone's checkout: use `readOnly` for most developers (above). Accepted as with `CMCODER.md` |
| ReDoS | The chunker's definition pattern stays linear on hostile 140 KB lines (5 ms) |
| Failure | A failing gateway or store never stops a turn (one warning); indexing runs beside the conversation |

## Scanner results, reviewed

| Tool | Result |
|---|---|
| Bandit | 0 high, 0 medium (SHA-1 for fingerprints and IDs marked `usedforsecurity=False`) |
| Semgrep | 27 findings: false positives (below) |
| pip-audit | chromadb: the 5 advisories above (documented); nothing else |
| npm audit | 0 vulnerabilities |

| Semgrep finding | Why it's not a problem |
|---|---|
| `insecure-hash-algorithm-sha1` (`rag/`) | Change fingerprints and IDs, not security; `usedforsecurity=False` |
| `regex_dos` (`rag/chunker.py`) | Measured linear (above) |
| `string-concat-in-list` (`cli/factory.py`) | One message split over two lines on purpose |
| `detect-generic-ai-*`, `detect-anthropic`, `useless-inner-function`, `is-function-without-parentheses` | Informational / code-quality heuristics, as in Phases 3–4 |
