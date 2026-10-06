# cmcoderagent — Design

Status: **Phases 0–5 complete; Phase 6 (other IDEs) planned** (see [README.md](README.md)) · Scope: architecture and roadmap

cmcoderagent is an agentic coding assistant that runs on the developer's
machine. Its behaviour and user experience mirror Claude Code (CLI and VS Code
extension), but it talks to **any OpenAI-compatible model endpoint**: cloud
APIs, or self-hosted Ollama, vLLM, LM Studio and llama.cpp servers.

**The model never runs on the dev machine.** The agent (tools, file edits,
shell commands) runs locally; the model is always reached over the network.

---

## 1. Decisions so far

| # | Question | Decision |
|---|---|---|
| D1 | Build on the Claude Agent SDK, or our own engine? | **Our own engine** with a provider layer. The SDK ties us to Claude models. |
| D2 | Model targets / hardware | **Mirror Claude Code**: no hardware requirement on the dev machine, because the model is always remote (D8). The model is a setting (`model`, `/model`, env var) and a small/fast model can be set separately. |
| D3 | Local-only or mixed? | **Mirror Claude Code**: online by default with any OpenAI-compatible endpoint, cloud or self-hosted. No telemetry backend; a single switch disables all non-essential traffic. |
| D4 | Primary wire protocol | **OpenAI Chat Completions.** Ollama is reached through its OpenAI-compatible `/v1` endpoint. |
| D5 | Shared/team model server | **Out of scope for now** on our side (no multi-user features). The client still copes with whatever auth and rate limits the remote server applies. |
| D6 | Language / stack | **Python 3.11+ for the core engine and CLI**; TypeScript only for the VS Code extension, which VS Code requires. See §3.1. |
| D7 | How the IDE uses the agent | The VS Code extension **runs the Python CLI as a child process** and talks to it over JSON over stdio, as Claude Code does. There is one engine. |
| D8 | Where the model runs | **Never on the dev machine.** Always a remote URL, so TLS, auth headers, corporate proxies/CAs, latency and network errors are first-class concerns (§4.3–4.5). |
| D9 | Reference deployment | A model server **on the company network**, reached over HTTPS through an internal domain name: `https://aiXIngerence.localnw.ae` (spelling to be confirmed). This is a user/project setting, never hard-coded. See §4.6. |
| D10 | Reference models | **Qwen3 ~7B** as the small/fast model and **Qwen3 ~27B** as the main model (exact IDs to be read from the server's `/v1/models`). See §5.1. |
| D11 | Product and command name | Product **cmcoder**; command `cmcoder`; config folder `.cmcoder/`; memory file `CMCODER.md`. |
| D12 | Model gateway | **LiteLLM proxy** exposing an OpenAI-compatible endpoint, with Qwen3 models behind it. See §4.7. |
| D13 | Authentication | **API key now** (LiteLLM virtual key, sent as `Authorization: Bearer`). Built behind an auth-provider interface. SSO (e.g. Okta/OIDC) is **not planned** (removed from Phase 4, 3 Oct 2026); the interface would let it be added without touching the rest of the engine. |
| D14 | TLS | Server uses **internal (company CA) certificates**. Trusted through the OS certificate store by default, with a CA-file override. |
| D15 | Second gateway | **Open WebUI** (its OpenAI-compatible `/api` endpoints, API key from the user's account), in addition to LiteLLM. Phase 4 item 1. |
| D16 | Developers' PCs | **May not have Python** (nor uv, pip or Node.js). Everything handed to developers carries the standalone `cmcoder` (PyInstaller) and never falls back to Python; the release workflow tests each file with no Python in reach (`packaging/no_python.py`). Git for Windows is still needed for shell commands on Windows. Decided 6 Oct 2026. |
| D17 | Chroma for code search | **Always by its URL** (for now): a Chroma running on the PC (`http://localhost:8000`, e.g. its Docker container) or a shared server. Chroma inside cmcoder (the `chromadb` package) isn't offered, since the standalone program (D16) can't include it; to be revisited. Decided 6 Oct 2026. |

## 2. Goals and non-goals

**Goals**
- Match Claude Code's core user experience: interactive TUI, `-p` headless mode,
  permission prompts, sessions/resume, project memory file, slash commands,
  MCP, hooks, subagents, and a VS Code extension with diff review.
- Work well with OpenAI-compatible servers, including mid-sized open-weight models hosted on another machine.
- Never send code anywhere except the configured model endpoint and tools the
  user approves (WebFetch, MCP servers).

**Non-goals (for now)**
- Multi-user servers, auth, rate limiting (D5).
- ~~JetBrains or other IDEs~~: now Phase 6 (JetBrains, Visual Studio 2022, Eclipse), on the same
  IDE-neutral protocol; see [phase6/PLAN.md](phase6/PLAN.md).
- Hosted or cloud execution of the agent itself.
- Running models on the dev machine.

## 3. Architecture

```
┌──────────────┐   ┌───────────────────┐   ┌──────────────┐
│  CLI / TUI   │   │ VS Code extension │   │ Headless -p  │
│  (Textual)   │   │  (TypeScript)     │   │ (json/stream)│
└──────┬───────┘   └─────────┬─────────┘   └──────┬───────┘
       │      Agent Protocol: JSON lines over stdio      │
       └──────────────┬──────┴─────────────────────────┘
               ┌──────▼───────┐
               │  Agent Core  │  loop · tools · permissions · context   (Python)
               └──────┬───────┘
  ┌──────────┬────────┼─────────┬───────────┬──────────┐
Provider   Model    MCP      Hooks      Sessions    Config
(OpenAI-   profiles client   runner     (JSONL)     (layered)
 compat)
   │
   │  HTTPS (TLS, API key / headers, proxy)
   ▼
Remote model server — cloud API, or Ollama / vLLM / LM Studio / llama.cpp on another host
```

### 3.1 Tech stack

| Area | Choice | Why |
|---|---|---|
| Language | **Python 3.11+**, fully type-hinted | Your preference; good fit for an I/O-bound agent |
| Concurrency | `asyncio` | Streaming, parallel tool calls, subprocesses and cancellation in one model |
| Packaging / env | `uv` + `pyproject.toml` (hatchling) | Fast installs, lockfile, `uv tool install cmcoder` |
| HTTP | `httpx` (async; HTTP/1.1 keep-alive, HTTP/2 optional later) with a built-in SSE parser | Full control of streaming and server quirks; proxy and custom CA support |
| Data models | `pydantic` v2 | Tool input schemas (gives JSON Schema for free), protocol messages, settings |
| CLI args | `typer` | Subcommands and flags with little code |
| TUI | `textual` + `rich` | Closest Python equivalent to Ink: streaming Markdown, panels, dialogs |
| Line editing (fallback/simple mode) | `prompt_toolkit` | History, multiline input, key bindings |
| MCP | official `mcp` Python SDK | Maintained client for stdio and HTTP transports |
| Search | bundled/required `ripgrep`; `pathspec` for `.gitignore` | Speed; same behaviour as Claude Code's Grep/Glob |
| Token estimates | Character counts, calibrated against the prompt tokens the server reports (§9) | No tokenizer download, which would fail on a locked-down network (tiktoken fetches its vocabulary at runtime) |
| Fuzzy edit matching | `rapidfuzz`, `difflib` | Edit fallback for whitespace drift |
| Secrets | `keyring` | API keys in the OS keychain |
| Corporate certificates | `truststore` | Trust the OS certificate store (internal CAs) without extra setup |
| Tests | `pytest`, `pytest-asyncio`, `respx` (mock httpx) | Provider quirks testable without a real server |
| Lint / types | `ruff`, `pyright` (strict on core) | |
| VS Code extension | TypeScript; webview UI in plain TypeScript, bundled with esbuild (Phase 2 decision) | VS Code extensions must be JS/TS |
| Protocol types across languages | pydantic → JSON Schema → generated TS types | One source of truth for Python and TypeScript |
| Distribution | PyPI (`uv tool` / `pipx`) first; later a standalone binary (PyInstaller or Nuitka) bundled in platform-specific VSIX packages | Extension users shouldn't need to manage Python |

**Python-specific risks and mitigations**
- *Startup time* (imports can add hundreds of ms): lazy-import heavy modules; keep `--protocol stdio` mode lean, since VS Code starts it once per session anyway.
- *Distribution*: users need Python 3.11+ until the standalone binary exists; `cmcoder doctor` checks the environment.
- *Persistent shell for Bash*: `asyncio` subprocess with sentinel markers to detect command end and capture exit codes. On Windows the shell is Git Bash (see §3.3).

### 3.2 Repository layout

```
cmcoderagent/
├── pyproject.toml            # one Python package: cmcoder
├── src/cmcoder/
│   ├── protocol/             # pydantic message types (source of truth for TS types)
│   ├── providers/            # internal message format, OpenAI-compatible adapter, model profiles
│   ├── core/                 # agent loop, permissions, context, sessions, hooks
│   ├── tools/                # Read, Write, Edit, Glob, Grep, Bash, WebFetch, Task, …
│   ├── mcp/                  # MCP client integration
│   ├── config/               # layered settings
│   └── cli/                  # typer entry point, Textual TUI, headless and stdio modes
├── tests/
├── docs/                     # DESIGN.md (living) + one folder per phase: PLAN, guides, STATUS
├── vscode/                   # TypeScript extension + webview
└── evals/                    # benchmark tasks, runner, mock model server
```

### 3.3 Windows support

cmcoder runs natively on Windows (not through WSL), with the same behaviour as on macOS and Linux:

| Area | Windows approach |
|---|---|
| Bash tool | **Git Bash** from Git for Windows, as Claude Code does. Found next to `git.exe`, on PATH, or in the standard install folders; `CMCODER_GIT_BASH_PATH` overrides. The WSL launcher (`System32\bash.exe`) is skipped because it sees a different filesystem. Permission rules (`Bash(npm test:*)`) work unchanged. |
| Killing commands | Commands run in their own process group; timeouts and Ctrl+C kill the whole tree with `taskkill /T`. |
| Ctrl+C | `loop.add_signal_handler` doesn't exist on Windows; a plain SIGINT handler forwards to the event loop instead. |
| Paths | File tools accept `C:\...`, `C:/...` and Git Bash `/c/...` paths; all paths are resolved before permission checks (8.3 short names). |
| Line endings | Write saves exactly what it's given; Edit keeps a file's CRLF endings; command output is normalised to LF. |
| Search | ripgrep if installed, otherwise a built-in Python search with the same .gitignore and glob semantics. |
| Certificates | Windows certificate store via `truststore`; with `caCertPath`, Python's default context (which also loads the Windows ROOT/CA stores) plus that file. |
| Piped input | `PeekNamedPipe` instead of `select` to detect `git diff \| cmcoder -p ...`. |
| Encoding | stdout/stderr forced to UTF-8; all file I/O is explicit UTF-8. |

CI runs the full test suite and evals on Windows (without ripgrep, to exercise the fallback), macOS and Linux.
The pseudo-terminal REPL tests are POSIX-only.

## 4. Provider layer (OpenAI-compatible first)

### 4.1 Internal message format
The core only ever sees provider-neutral types:

- `system` · `user` (text, images) · `assistant` (text, `reasoning`, `tool_calls[]`) · `tool` (call id, result, is_error)
- Stream events: `text_delta`, `reasoning_delta`, `tool_call_start/delta/end`, `usage`, `stop(reason)`

### 4.2 OpenAI Chat Completions adapter
Built directly on `httpx`, not the `openai` SDK, so we control parsing of
non-standard fields and errors. Uses `POST {baseUrl}/chat/completions` with `stream: true`, `tools`, `tool_choice`,
and `stream_options.include_usage` when the server supports it.

It must cope with these known differences between servers:

| Quirk | Handling |
|---|---|
| Tool-call arguments streamed in pieces by `index`, or sent all at once | Collect by index and parse when the stream ends |
| Missing or duplicate `tool_call.id` | Generate stable ids |
| No `usage` in the stream | Estimate tokens with a tokenizer for the model, or ~4 chars per token |
| `max_tokens` vs `max_completion_tokens`; `system` vs `developer` role | Set per model profile |
| Reasoning in `reasoning_content`, `reasoning`, or `<think>…</think>` in text | Normalise to `reasoning` and don't send it back in later requests unless the profile says to |
| No parallel tool calls | Set `parallel_tool_calls: false` (profile `parallelToolCalls`; on for Qwen3, so subagents can run in parallel) and run other calls one at a time |
| `content: null` with tool calls; empty assistant turns | Clean up before sending |
| HTTP 429 / 5xx / dropped stream | Retry with backoff and jitter, honouring `Retry-After`; resume the turn |
| Slow first token on a busy or cold remote server (model loading) | Separate connect, first-token and idle-stream timeouts; show "waiting for model" in the UI |

Other API formats (OpenAI **Responses API**, native **Anthropic**) are **not planned**: both supported gateways (LiteLLM, Open WebUI) speak the chat-completions format (decided 3 Oct 2026).

### 4.3 Remote Ollama notes
- Reached at `https://<host>/v1` (or `http://<host>:11434/v1` on a trusted network).
  The Ollama host must listen beyond localhost (`OLLAMA_HOST=0.0.0.0`).
- **Ollama has no built-in authentication.** Recommend putting it behind a reverse
  proxy (nginx, Caddy) that adds TLS and an API key or bearer token; the agent
  sends it via `apiKey` or `headers`.
- **Context window:** the OpenAI-compatible endpoint does not take Ollama's
  `num_ctx`, and Ollama's default window is small enough that the system prompt
  plus tools can be cut off without warning. Mitigations:
  1. `cmcoder doctor` checks the effective context size via `/api/show` (if the proxy exposes it) and warns.
  2. Documentation: set `OLLAMA_CONTEXT_LENGTH` on the server, or create a custom model with a larger `num_ctx`.
  3. Optional later: a native `/api/chat` adapter that sets `num_ctx` on each request.
  *(Check the current Ollama defaults and options when we implement this.)*

### 4.4 Endpoint configuration (Claude Code-style)
| Setting | Env var | Meaning |
|---|---|---|
| `baseUrl` | `CMCODER_BASE_URL` (falls back to `OPENAI_BASE_URL`) | OpenAI-compatible endpoint |
| `apiKey` / `apiKeyHelper` | `CMCODER_API_KEY` (`OPENAI_API_KEY` only with `OPENAI_BASE_URL`) | Static key, or a command that prints one |
| `headers` | `CMCODER_CUSTOM_HEADERS` | Extra HTTP headers (e.g. a gateway token) |
| `model` | `CMCODER_MODEL` | Main model; `/model` switches it in a session |
| `smallFastModel` | `CMCODER_SMALL_FAST_MODEL` | Used for titles, summaries, quick classification |
| `subagentModel` | — | Default model for subagents (falls back to `model`) |
| — | `CMCODER_DISABLE_NONESSENTIAL_TRAFFIC=1` | No update checks or anything other than model, WebFetch and MCP calls |

### 4.5 Network and transport (remote model)
- TLS verification always on; custom CA bundle via `caCertPath` / `SSL_CERT_FILE` for corporate or self-signed servers.
- `HTTPS_PROXY` / `NO_PROXY` respected. (mTLS client certificates: later, if the gateway needs them.)
- Connection pooling with keep-alive to cut per-turn latency.
- Plain `http://` to a non-localhost host prints a one-time warning, since code is sent in clear text.
- `cmcoder doctor` tests reachability, TLS, auth, the model list (`GET /models`), streaming and tool calling, and reports latency to first token.

### 4.6 Reference deployment (company network)

The first target environment, and the one evals and `doctor` are tuned for:

```jsonc
// ~/.cmcoder/settings.json  (or .cmcoder/settings.json committed for the team)
{
  "providers": {
    "corp": {
      "baseUrl": "https://aiXIngerence.localnw.ae/v1",  // confirm exact host and path
      "auth": { "type": "apiKey" },                     // key from CMCODER_API_KEY or OS keychain
      "caCertPath": "/path/to/corp-root-ca.pem"         // optional; OS trust store is used first
    }
  },
  "model": "corp:<qwen3-27b-id>",
  "smallFastModel": "corp:<qwen3-7b-id>"
}
```

Things this setup needs, all covered by §4.5:
- **Internal certificate authority (confirmed):** Python does not read the
  Windows or macOS certificate store by default, so we use `truststore` to trust
  the OS store automatically (where IT has already installed the company root CA),
  with `caCertPath` / `SSL_CERT_FILE` as a fallback. `doctor` shows the
  certificate chain and names the missing CA when verification fails.
  Verification is never switched off. If the VS Code extension ever makes
  HTTPS calls itself, Node needs `NODE_EXTRA_CA_CERTS`; today only the Python
  CLI talks to the server.
- **Internal DNS:** the name only resolves on the company network or VPN.
  `doctor` reports DNS failure separately from TLS and auth failures so users
  know whether to connect the VPN or fix a certificate.
- **Proxies:** if a corporate proxy is set, `.localnw.ae` must be in `NO_PROXY`;
  `doctor` detects and warns about this.
- **Authentication (confirmed: API key):** `cmcoder login` asks for the LiteLLM
  key once and stores it in the OS keychain; `CMCODER_API_KEY` overrides it for
  CI. The key is never written to settings files or session logs.

**Auth-provider interface.** All requests get their credentials from one
interface (`get_headers()`, `refresh()`, `on_401()`), so adding SSO later is a
new provider, not a rewrite:

| Provider | When | How |
|---|---|---|
| `apiKey` | Phase 0 | Static key from keychain/env, `Authorization: Bearer` |
| `apiKeyHelper` | Phase 1 | Runs a command that prints a key (works with any company script) |
| `oidc` (e.g. Okta) | Not planned (removed 3 Oct 2026) | Device-code sign-in in the browser, token cached in keychain, refreshed automatically; LiteLLM validates the JWT |

### 4.7 LiteLLM gateway specifics

LiteLLM sits between cmcoder and the Qwen3 servers. What we use and handle:

- **Model names are LiteLLM aliases** set by the admin (e.g. `qwen3-27b`), not
  the underlying model files. `GET /v1/models` lists the aliases the key may use;
  `/model/info` (when exposed) gives context window, max output and
  function-calling support, which `doctor` uses to fill model profiles automatically.
- **Tool calling still depends on the backend behind LiteLLM** (e.g. vLLM needs
  its tool parser enabled). LiteLLM passes tools through but cannot add tool
  calling to a backend that lacks it. `doctor --probe` tests it per model; the
  prompted-tool fallback covers gaps.
- **Qwen3 thinking switch:** sent as extra request fields (e.g.
  `chat_template_kwargs.enable_thinking`). Whether LiteLLM forwards them
  depends on its config, so `doctor` tests it and falls back to the `/no_think`
  prompt switch.
- **Reasoning text** may come back in a `reasoning_content` field (LiteLLM's
  normalised form) or as `<think>` tags; both are handled (§4.2).
- **Budgets and rate limits per key:** LiteLLM can reject requests when a key's
  budget or rate limit is hit. These errors are shown clearly ("key budget
  exceeded — contact the LiteLLM admin") and are not retried in a loop.
- **Cost:** if LiteLLM returns a response-cost header, `/cost` shows it;
  otherwise cost is token counts only.
- **Fallbacks and retries:** LiteLLM may retry or route to a fallback model
  itself; cmcoder keeps its own retries small to avoid multiplying them, and
  records which model actually answered (from the response's `model` field).
- **Privacy:** LiteLLM can log prompts and responses. The user docs must say
  that the gateway admins may be able to see code sent to the model.

Named **provider profiles** let users keep several endpoints (e.g. `local`,
`openai`, `openrouter`) and switch between them with `/model local:qwen3-coder`.

## 5. Model profiles

Models behave very differently, so each one is described by a profile:

```jsonc
{
  "match": "qwen3-coder*",
  "contextWindow": 131072,
  "maxOutput": 16384,
  "toolCalling": "native",          // native | prompted | native-unreliable
  "parallelToolCalls": false,
  "reasoning": "think-tags",        // none | field | think-tags
  "editFormat": "str_replace",      // str_replace | search_replace_blocks | whole_file | udiff
  "promptTier": "full",             // full | compact
  "temperature": 0.2,
  "tokenizer": "approx"
}
```

- Built-in profiles for common models; user overrides in settings.
- Context window, most trusted last: built-in default < LiteLLM `/model/info` <
  what the server enforces (learned from its "maximum context length is N" error, or
  from a one-off probe with an over-large `max_tokens`; cached per gateway and model)
  < an explicit `contextWindow` in settings. `cmcoder doctor` shows the value and its
  source (Phase 1 item 3).
- Unknown models get conservative defaults; `cmcoder doctor --probe` runs a short
  tool-calling test against the endpoint and suggests a profile.

### 5.1 Reference models and their roles

| Role | Model | Profile highlights |
|---|---|---|
| Main agent (`model`) | **Qwen3 ~27B** | Native tool calling, `full` prompt tier, `str_replace` edits with fuzzy fallback, thinking on for planning turns |
| Small/fast (`smallFastModel`) | **Qwen3 ~7B** | Titles, compaction summaries, command-safety checks, simple read-only subagents; `compact` prompt tier, thinking off |

Notes for the Qwen3 family:
- **Thinking mode:** Qwen3 writes reasoning in `<think>…</think>`. It is shown
  collapsed in the UI and never sent back to the model. Thinking is switched off
  for small, quick calls (via the `/no_think` switch or the server's
  `enable_thinking` chat-template option, whichever the server supports) to save latency.
- **Tool calling depends on the server** (Phase 1: cmcoder also parses `<tool_call>` text from a backend without a tool parser, and switches to prompted tool calls in Qwen's format if `tools` is rejected): vLLM needs tool calling enabled with
  the Hermes parser; Ollama supports Qwen3 tools natively. `doctor --probe`
  checks it, and the prompted-tool fallback (§6) covers servers where it is off.
- **Context window:** Qwen3's native window is 32K tokens, extendable (e.g. with
  YaRN) if the server is configured for it. Compaction thresholds come from the
  real served window, not an assumption.
- **Expectations for ~7B:** it can't reliably drive multi-step edits on its own,
  so it is never the default main model. Users can still choose it with `/model`.
- **Sampling:** start from Qwen's recommended settings for each mode (lower
  temperature for non-thinking turns) and tune with evals.

## 6. Agent loop

1. Build the request: system prompt (stable prefix) → tools → memory files → conversation → dynamic context (date, git status, IDE selection) at the end.
2. Stream the reply and send events to the front end as they arrive.
3. For each tool call: validate against the schema → run PreToolUse hooks → permission check → run → PostToolUse hooks → append the result.
4. Repeat until the model stops calling tools, the user interrupts, or a turn/token budget is reached.
5. Check context usage after each step and compact if needed (§9).

**Making tool calls reliable on weaker models**
- **Prompted tool calling** for profiles without native support: tools are
  described in the system prompt and calls are written as tagged blocks, then parsed.
- **Repair:** fix malformed JSON automatically, then validate with pydantic; on failure,
  send a short, specific error back to the model (at most N retries per call).
- **Loop detection:** stop identical repeated calls and tell the model.
- **Keep the prompt prefix stable** so server-side KV/prefix caches (vLLM, llama.cpp, Ollama) and provider prompt caching (OpenAI) are reused; this also cuts latency over the network.

## 7. Tools (match Claude Code)

| Tool | Notes |
|---|---|
| `Read` | Line ranges, line numbers, image/PDF/notebook support later; tracks which files have been read |
| `Write` | Refuses to overwrite a file not read in this session |
| `Edit` | Exact string replacement, `replace_all`; format chosen by model profile; fuzzy fallback for whitespace differences |
| `MultiEdit` | Several edits to one file applied together (all or none) |
| `Glob` | Fast pattern matching, respects `.gitignore` |
| `Grep` | Bundled ripgrep |
| `Bash` | Persistent shell, timeouts, background jobs, output truncation, sandbox (§8) |
| `WebFetch` / `WebSearch` | Permission-gated; search backend configurable |
| `TodoWrite` | Task list shown in the UI |
| `Task` | Starts a subagent with its own context, tools and model |
| `NotebookEdit` | Later |
| IDE tools | Only when running under VS Code: `getDiagnostics`, `openFile`, `showDiff` |

Tool descriptions are part of the prompt tiers: the `compact` tier uses shorter descriptions and fewer tools.

## 8. Permissions and sandbox

- **Modes:** `default` (ask), `acceptEdits`, `plan` (read-only, then propose a plan), `bypassPermissions`. Shift+Tab cycles through them in the TUI. Managed settings can disable `bypassPermissions` everywhere (§11).
- **Rules:** `allow` / `ask` / `deny` lists in settings, e.g. `Bash(npm test:*)`, `Edit(src/**)`, `WebFetch(domain:github.com)`, `mcp__server__tool`. Deny wins.
- **Interactive prompt:** allow once / allow always (saved to `settings.local.json`) / deny with a reason for the model.
- **Working directory boundary:** writes outside the project and added directories need approval.
- **Protected paths:** editing `.cmcoder/**`, `.git/**` or `~/.cmcoder/` always asks (even in `acceptEdits`, even with a broad allow rule), so the agent can't grant itself permissions or plant git hooks. Only `bypassPermissions` skips this.
- **Read-only commands** run without asking only with read-only arguments: `git branch` lists but `git branch -D` asks; `git diff --output=…` and `date -s` ask; `git -c … diff` asks.
- **Secret files** (`.env*`, `*.pem`, `*.key`, `/secrets`, …) are blocked for Read/Edit/Grep targets and excluded from project-wide Grep results. Bash is not filtered: `cat .env` asks in the default modes.
- **High-risk commands** (implemented after Phase 0, at the security team's request) always ask, in every mode including `bypassPermissions`, and allow rules cannot pre-approve them; "always allow" is not offered, and non-interactive runs (`-p`) refuse them. They are: recursive/forced/wildcard `rm`, `find -delete`, `git clean`, `git reset --hard`, `git checkout -f`/`-- .`, `git restore`, force pushes and remote deletes, `git branch -D`, `git stash drop/clear`, history rewrites, `sudo`/`su`, `dd`, `shred`, `truncate`, `mkfs`, recursive `chmod`/`chown`, `rsync --delete`, `curl … | sh`, and the Windows forms (`del /s`, `rd /s`, `Remove-Item -Recurse`, encoded PowerShell, `reg` changes). Commands are found inside `;`/`&&`/`|` chains, `$( )`, backticks, `bash -c`, `cmd /c`, `powershell -Command`, `eval`, `xargs`, `env`, `sudo`. `permissions.highRiskCommands: "deny"` blocks them outright; the strictest settings layer wins, so a project file cannot relax a user's `deny`. Code: `core/risk.py`. This is a safety net against model mistakes, not a sandbox: text matching can be evaded on purpose.
- **Bash sandbox (opt-in, then default):** bubblewrap on Linux, Seatbelt on macOS; filesystem writes limited to the project, network limited to an allowlist.
- Commands are checked for injection tricks (e.g. `;`, `$(...)`, `&&` chains) before an allow rule is applied to them.

## 9. Context and memory

- **Memory files**, loaded at startup in this order:
  enterprise → `~/.cmcoder/CMCODER.md` → `CMCODER.md` in each folder from the repo root down to the current one → `CMCODER.local.md`.
  For compatibility, `AGENTS.md` is also read if present. (`#` in the prompt to add a line to memory: Phase 1.)
- **Context budget (Phase 0):** each request's `max_tokens` is capped to the room left in the window (servers like vLLM reject prompt + `max_tokens` > window). When the window fills, the oldest large tool outputs are replaced with a short note (keeping the latest ones), as Claude Code clears old tool results; a server context-length error triggers one retry after freeing more. Token counts are estimated from characters and calibrated against the server's reported usage.
- **Auto-compaction (Phase 1, done)** at a set share of the model profile's `contextWindow` (`autoCompactThreshold`, default 0.8; `autoCompact: false` turns it off): the small/fast model (main model as fallback) summarises older turns, in chunks if they exceed its own window; the newest ~25% of the window is kept as it is, cut at a step boundary so tool calls keep their results; the user's latest request is carried word for word. `/compact [focus]` runs it by hand. If summarising fails, the Phase 0 behaviour (dropping old tool output) applies. The todo list (item 8) will be kept across compaction.
- **Tool-output limits:** each output is capped at about a fifth of the context window (≤ 30k chars). Read stops at a line boundary and tells the model which `offset` to continue from; command output keeps head and tail. (Saving the full text to a file for later reading: Phase 1.)
- `@file` mentions attach file contents; `/context` shows how the window is being used.

## 10. Sessions

- Saved as JSONL under `~/.cmcoder/projects/<project-hash>/<session-id>.jsonl`.
- `--continue`, `--resume [id]`, `/resume` picker. *(Phase 1, done: JSON Lines with `message` / `reset` / `title` records, owner-only files, repaired on load if cut off mid-tool; `persistSessions`, `cleanupPeriodDays`.)*
- **Checkpoints:** file state saved before each edit; `/rewind` restores code, the conversation, or both. *(Phase 1, done: content-addressed snapshots per turn next to the session file; Write/Edit only, Bash changes are not tracked; files outside the project only with consent.)*
- Cost and token use are tracked per session (`/cost`); prices per model are configurable and may be 0 for local models.

## 11. Configuration

Layers 2–5 are merged in order, later ones overriding earlier ones (permission rules accumulate instead). Layer 1 is applied last and cannot be overridden:

1. Managed settings (enterprise policy, Phase 1), admin-only: `/etc/cmcoder/managed-settings.json` (Linux), `/Library/Application Support/cmcoder/managed-settings.json` (macOS), `C:\Program Files\cmcoder\managed-settings.json` (Windows). Always wins; fails closed if broken; can't be moved by env var or flag (on Windows the folder comes from the Known Folders API). Keys: `permissions.disableBypassPermissionsMode`, `permissions.highRiskCommands`, `permissions.deny`, `permissions.allowManagedPermissionRulesOnly`, `lockProviders`, `env`; the managed-only keys are ignored in every other file. Example: [managed-settings.example.json](managed-settings.example.json). Implemented in Phase 1 (item 5).
2. User `~/.cmcoder/settings.json`
3. Project `.cmcoder/settings.json` (committed)
4. Local project `.cmcoder/settings.local.json` (git-ignored)
5. CLI flags / env vars

Keys: `model`, `providers`, `modelProfiles`, `permissions`, `hooks`, `env`, `mcpServers`, `sandbox`, `statusLine`, `outputStyle`, `cleanupPeriodDays`.

## 12. Extensibility

- **MCP client:** stdio, Streamable HTTP and SSE transports; servers configured in `.mcp.json` (project) and settings (user); tools, resources and prompts are supported; `/mcp` shows status. Tool names are `mcp__<server>__<tool>`.
- **Hooks:** shell commands run on `PreToolUse`, `PostToolUse`, `UserPromptSubmit`, `SessionStart`, `Stop`, `SubagentStop`, `PreCompact`, `Notification`. They receive JSON on stdin and can block or change an action through exit code or JSON output.
- **Slash commands:** built-in (`/help`, `/model`, `/compact`, `/clear`, `/cost`, `/resume`, `/permissions`, `/mcp`, `/init`, `/doctor`, …) plus custom Markdown commands in `.cmcoder/commands/` and `~/.cmcoder/commands/`, with `$ARGUMENTS`.
- **Subagents:** Markdown definitions in `.cmcoder/agents/` (name, description, tools, model, prompt), started by the `Task` tool.
- **Skills:** folders with `SKILL.md`; only the description is loaded until the model decides to use one.
- **Output styles and status line:** later.

## 13. Front ends

### 13.1 CLI / TUI (`cmcoder`)

*Phase 1: the classic prompt_toolkit REPL is the default; the full-screen Textual UI is available with `cmcoder --tui` or `"ui": "textual"` (fixed-size permission dialog with a scrollable preview) and becomes the default once verified on Windows.*
- Textual-based: streaming Markdown, tool-call cards, permission dialogs, diff previews, todo panel, Esc to interrupt, history, `!` to run a shell command directly.
- `cmcoder -p "…" [--output-format text|json|stream-json] [--max-turns N] [--allowedTools …]` for scripts and CI.

### 13.2 Agent Protocol (stdio)
`cmcoder --protocol stdio` reads and writes newline-delimited JSON:
- **Agent → client (implemented in Phase 0, `src/cmcoder/protocol/events.py`):** `system_init`, `assistant_delta`, `reasoning_delta`, `assistant_message`, `tool_use`, `tool_result`, `permission_denied`, `usage`, `warning`, `error`, `result`. Phase 2 adds `permission_request`, `ide_tool_request` and `todo_update`.
- **Client → agent (Phase 2):** `user_message`, `interrupt`, `permission_response`, `set_mode`, `set_model`, `ide_context` (open file, selection, diagnostics), `ide_tool_result`.

The protocol is versioned and has no VS Code-specific types, so other IDEs can use it later.

### 13.3 VS Code extension
- Chat panel (webview) showing the same events as the TUI; session list and resume.
- Proposed edits open in VS Code's **native diff editor** with accept/reject; accepting makes the edit.
- Sends the active file, selection and Problems-panel diagnostics automatically (can be turned off); `@` file picker; "Ask cmcoder about selection" command.
- Mode and model pickers; a terminal option that runs the TUI inside VS Code's terminal.
- Settings UI writes to the same `.cmcoder/settings*.json` files as the CLI.

## 14. Privacy and network

- Because the model is always remote, **code and prompts leave the machine** on every turn, but **only** to the configured model endpoint, plus WebFetch/WebSearch and MCP servers the user has approved. The docs must say this clearly.
- Files matching deny rules (e.g. `.env`, `*.pem`, `secrets/**`) are blocked from `Read` by default so they are never sent to the model.
- No telemetry backend. Optional OpenTelemetry export (metrics/logs) to an endpoint the user chooses, off by default.
- `CMCODER_DISABLE_NONESSENTIAL_TRAFFIC=1` turns off update checks.
- API keys are read from env vars, `apiKeyHelper`, or the OS keychain, never written to session logs.

## 15. Evaluation

Started early, because quality depends heavily on the model:

- `evals/tasks/*`: small repos, each with a task prompt and a check script (tests pass, file matches, etc.). Phase 1: 20 tasks; each check is verified to fail on the untouched repo (`tests/test_evals.py`); the runner also scores tool choice.
- Categories: read-only Q&A, single-file fix, multi-file refactor, run-tests-and-fix, tool-use hygiene (no blind overwrites, respects denials).
- Runner reports success rate, turns, tokens, tool-error rate and time, per model and profile.
- Runs in CI on every core change against a **mock OpenAI-compatible server** that replays recorded responses (no GPU or API key needed), and fully against real remote endpoints before releases.

## 16. Roadmap

| Phase | Scope | Done when |
|---|---|---|
| **0 — Foundations** | Python package skeleton (uv, ruff, pyright, pytest), protocol types, provider layer + OpenAI-compatible adapter (TLS, headers, retries), model profiles, agent loop, Read/Write/Edit/Glob/Grep/Bash, basic TUI, `-p`, `doctor`, mock server, first ~20 eval tasks | Fixes a simple bug end-to-end against the LiteLLM gateway with the reference Qwen3 models |
| **1 — Daily driver** | **Auto-compaction first** (the 32K default window is the binding constraint, §16.2), **the user-trial fixes in §16.3**, Textual TUI, sessions/resume, small/fast model jobs (titles, summaries), prompted-tool fallback and repair, edit-format variants, TodoWrite, checkpoints, **managed settings** (moved from Phase 4, see [phase1/PLAN.md](phase1/PLAN.md) item 5) | Comfortable for daily use on a real repo |
| **2 — VS Code** | `--protocol stdio`, generated TS protocol types, extension, webview chat, native diffs, IDE context and tools | Same task behaves the same in CLI and VS Code |
| **3 — Extensibility** | MCP client, hooks, custom slash commands, subagents (`Task`) with per-role models, skills | Teams can customise it without forking |
| **4 — Hardening** | **Open WebUI gateway**, Bash sandbox (Linux, macOS, and Windows through WSL2; moved from Phase 3), OpenTelemetry (off by default), standalone binary + platform-specific VSIX, branding (icon and name). SSO and the Responses/Anthropic adapters were dropped (see [phase4/PLAN.md](phase4/PLAN.md)) | Release candidate |
| **5 — Code search (RAG)** | Embeddings through the gateway, an incremental index of the project in a vector store (built-in local store, or Chroma by its URL: on the machine or a shared server; D17), a `CodeSearch` tool and automatic context; embeddings from LiteLLM or Open WebUI (see [phase5/PLAN.md](phase5/PLAN.md)) | The model finds code by meaning in large repositories; fewer turns and tokens in the evals |

Per-phase plans, guides and status reports live in [docs/README.md](README.md) (`docs/phase0/`, `docs/phase1/`, …). Finished phases are frozen as git snapshots (Phase 0: commit `ba6669f`, tag `phase0`; Phase 1: tag `phase1`; Phase 2: tag `phase2`; Phase 3: tag `phase3`).

### 16.1 Phase 0 status

**Phase 0 is complete**; see [phase0/STATUS.md](phase0/STATUS.md). **Phase 1 is complete**; see [phase1/STATUS.md](phase1/STATUS.md). **Phase 2 is complete**; see [phase2/STATUS.md](phase2/STATUS.md). **Phase 3 is complete**; see [phase3/STATUS.md](phase3/STATUS.md). **Phase 4 (Hardening) is complete**; see [phase4/STATUS.md](phase4/STATUS.md). **Phase 5 (Code search) is complete**; see [phase5/STATUS.md](phase5/STATUS.md).


Delivered:
- **Provider:** OpenAI-compatible streaming adapter on httpx (tool-call assembly, `reasoning_content` and
  `<think>` splitting including Qwen3 Thinking-2507 output that has only `</think>`, usage or estimates, LiteLLM cost header, `/models`, `/model/info`); error
  classification (auth, budget, rate limit, context length, TLS, DNS) with hints; retries with backoff and
  `Retry-After`; OS trust store via `truststore` plus `caCertPath`; proxy and custom headers.
- **Auth:** API key from `CMCODER_API_KEY` or OS keychain (`cmcoder login`/`logout`), with a 0600 file
  fallback where no keychain exists; `AuthProvider` interface ready for SSO.
- **Model profiles:** built-in Qwen3 large/small profiles, user overrides, server-reported limits.
- **Agent loop:** tool-argument repair and validation, unknown-tool and repeated-call handling, permission
  flow (ask / allow always / deny with feedback), max turns, context budget (`max_tokens` sized to the
  window, old tool output dropped when full, retry on overflow), clean history after interrupts and errors. Read-only tools still run one at a time.
- **Tools:** Read (pages long files), Write, Edit (read-before-write, staleness check, CRLF-safe), Glob and
  Grep (ripgrep, or a built-in fallback with the same semantics),
  Bash (persistent shell, timeouts that kill the process group, stdin closed).
- **Permissions:** the four modes, allow/deny rules, read-only commands checked by argument, chained-command
  protection, protected paths, secret-file protection (including Grep results), rules saved to
  `settings.local.json`.
- **Settings and memory:** layered settings + env vars (settings `env` applies to the session);
  `CMCODER.md`/`AGENTS.md`/`CMCODER.local.md`.
- **CLI:** interactive REPL (prompt_toolkit + rich: streaming Markdown, permission dialogs with diffs,
  Shift+Tab modes, Ctrl+C interrupt, `/help /model /mode /clear /cost /exit`); `-p` with text/json/stream-json
  and piped stdin; `doctor` (local tools, proxy, DNS, TCP, TLS chain and issuer, key, models, model info,
  streaming latency, thinking switch, tool calling); `models`; `protocol-schema`.
- **Testing:** unit, CLI end-to-end, TLS against a generated private CA, pseudo-terminal REPL tests, and
  gateway tests through a real LiteLLM proxy; eval runner with 5 tasks (mock and real-model modes); CI on
  Linux, macOS and Windows.

Changed from the plan:
- **Python 3.11+** instead of 3.12+, so it runs on more corporate machines.
- **Windows support** moved from Phase 4 into Phase 0 (§3.3).
- **Basic TUI uses prompt_toolkit + rich**; the Textual UI moves to Phase 1.
- **5 eval tasks**, not ~20; the rest are added in Phase 1 alongside real-model runs.
- **Not yet verified against the real gateway and Qwen3 models**, which this development environment cannot
  reach. The first step on the company network is `cmcoder doctor`, then `evals/run.py`.

### 16.2 Validation of the plan and Phase 0

Done in this repository, without access to the company network:

| What | How | Result |
|---|---|---|
| Plan → code traceability | Every Phase 0 item in §16 checked against the code and tests | Delivered, except the deviations listed in §16.1 |
| LiteLLM assumptions (§4.7) | Real LiteLLM proxy in front of a scripted vLLM-style backend (`tests/test_litellm_integration.py`, own CI job) | `/v1/models` and `/model/info` as assumed; `chat_template_kwargs`, `parallel_tool_calls`, `stream_options` forwarded; `reasoning_content`, tool-call deltas and usage streamed back; `doctor` and `-p` pass end to end |
| Internal CA (§4.6) | Tests generate a private root CA and serve HTTPS with it | Clear error without the CA; works with `caCertPath`; `doctor` names the issuer |
| Windows (§3.3) | CI on windows-latest with Git Bash, without ripgrep | Full suite and evals pass |
| Adversarial review | Probes against permissions, search, provider and agent | Found and fixed: write-capable "safe" commands, editable settings/git hooks in `acceptEdits`, secrets visible via project-wide Grep, credentials readable on approval, Qwen3 Thinking-2507 output (`</think>` only), LiteLLM auth errors as HTTP 400, and the context issues below |
| Context window (Qwen3, 32K) | Measured: ~1.9K tokens fixed overhead; one 2,000-line Read ≈ 8.5K tokens | Requests with a fixed `max_tokens=8192` failed once the prompt passed ~24.5K, and one overflow broke the session. Fixed with the context budget (§9). |

Not validated (needs the company network): the real gateway URL and certificate, the real Qwen3 models' tool-calling quality, and real latency. Run `cmcoder doctor` and `uv run python evals/run.py` there.

**Main plan risk:** a 32K window is small for an agent. Phase 0 now degrades gracefully (drops old tool output), but long tasks will lose context. Mitigations, in order: ask the admin to serve a longer context if GPU memory allows; make auto-compaction the first Phase 1 item; keep the small model for side jobs only.

### 16.3 Phase 1 backlog from user trials (Windows, real gateway, Qwen3.6-27B)

Found while using Phase 0 on a real project; all are Phase 1 work.

1. **Permission prompt pushed off screen by long previews (bug).** *Fixed in Phase 1 (item 2); the Textual dialog follows in item 11.* When the model
   writes a whole file through Bash (`cat > file << EOF … EOF`), the preview prints
   every line, the panel grows taller than the window, and the 1/2/3 options scroll
   out of view. Fix:
   - cap the preview height (first and last lines plus "… N more lines"; full text on
     request, e.g. `v` to view);
   - always draw the options **after** the preview, and repeat them in the input line
     (`1 yes · 2 always · 3 no:`);
   - on invalid input (e.g. `11`), re-show the options instead of a bare prompt;
   - in the Textual UI (Phase 1), render the prompt as a fixed-height dialog with a
     scrollable preview, so the options can never scroll away.
2. **Model uses Bash for file work.** *Addressed in Phase 1 (item 4): prompt rule, redirect of plain file work to the file tools, tool-choice evals; real-gateway measurement pending.* Qwen3.6 writes files with Bash heredocs and reads
   them with `python -c "open(...)"`, instead of the Write/Read/Glob tools. This bypasses
   read-before-write checks, causes permission prompts, and leads to item 1. Fix: tune
   the system prompt and tool descriptions for Qwen, detect common patterns
   (`cat > f <<`, `echo … > f`, `python -c "open(…)"`) and tell the model to use the
   proper tool, and add evals that score which tools are used.
3. **Real context window unknown.** *Fixed in Phase 1 (item 3): probed and learned from the server.* The gateway doesn't expose `/model/info`, so cmcoder
   assumes 32K for Qwen3. Get the real value from the admin, or detect it automatically
   from the server's "maximum context length is N" error, and cache it.

## 17. Open questions

1. ~~Product name~~ — decided: **cmcoder** (D11).
2. Licence and distribution (internal only vs. public PyPI/Marketplace).
3. Windows support timing (sandbox and shell behaviour differ a lot).
4. Default web search backend (needs an API key, or off by default?).
5. ~~Reference models~~ — decided: Qwen3 family (D10). The exact LiteLLM aliases and context windows will be read from `/v1/models` and `/model/info` by `doctor`; not blocking.
6. ~~Server, gateway, auth, TLS~~ — decided (D9, D12–D14). Still to confirm, not blocking Phase 0:
   - the exact hostname spelling;
   - whether a corporate HTTP proxy sits between dev machines and the server;
   - which backend LiteLLM routes to (vLLM, Ollama, …), in case tool calling needs enabling there.
7. Which SSO provider, if any, and when (D13).
8. Can the gateway serve the Qwen3 models with a context longer than 32K (§16.2)? Which Qwen3 variants exactly (hybrid, Instruct-2507 or Thinking-2507)? This affects thinking handling and default profiles.
