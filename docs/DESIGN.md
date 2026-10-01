# cmcoderagent — Design

Status: **Draft for discussion** · Scope: architecture and roadmap only (no code yet)

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
| D6 | Language / stack | **Python 3.12+ for the core engine and CLI**; TypeScript only for the VS Code extension, which VS Code requires. See §3.1. |
| D7 | How the IDE uses the agent | The VS Code extension **runs the Python CLI as a child process** and talks to it over JSON over stdio, as Claude Code does. There is one engine. |
| D8 | Where the model runs | **Never on the dev machine.** Always a remote URL, so TLS, auth headers, corporate proxies/CAs, latency and network errors are first-class concerns (§4.3–4.5). |

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
- JetBrains or other IDEs (the protocol is kept IDE-neutral so they can be added later).
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
| Language | **Python 3.12+**, fully type-hinted | Your preference; good fit for an I/O-bound agent |
| Concurrency | `asyncio` | Streaming, parallel tool calls, subprocesses and cancellation in one model |
| Packaging / env | `uv` + `pyproject.toml` (hatchling) | Fast installs, lockfile, `uv tool install cmcoder` |
| HTTP | `httpx` (async, HTTP/2) + `httpx-sse` | Full control of streaming and server quirks; proxy and custom CA support |
| Data models | `pydantic` v2 | Tool input schemas (gives JSON Schema for free), protocol messages, settings |
| CLI args | `typer` | Subcommands and flags with little code |
| TUI | `textual` + `rich` | Closest Python equivalent to Ink: streaming Markdown, panels, dialogs |
| Line editing (fallback/simple mode) | `prompt_toolkit` | History, multiline input, key bindings |
| MCP | official `mcp` Python SDK | Maintained client for stdio and HTTP transports |
| Search | bundled/required `ripgrep`; `pathspec` for `.gitignore` | Speed; same behaviour as Claude Code's Grep/Glob |
| Token estimates | `tiktoken` (approximate for non-OpenAI models) | Context budgeting when the server doesn't report usage |
| Fuzzy edit matching | `rapidfuzz`, `difflib` | Edit fallback for whitespace drift |
| Secrets | `keyring` | API keys in the OS keychain |
| Tests | `pytest`, `pytest-asyncio`, `respx` (mock httpx) | Provider quirks testable without a real server |
| Lint / types | `ruff`, `pyright` (strict on core) | |
| VS Code extension | TypeScript, webview UI in React + Vite | VS Code extensions must be JS/TS |
| Protocol types across languages | pydantic → JSON Schema → generated TS types | One source of truth for Python and TypeScript |
| Distribution | PyPI (`uv tool` / `pipx`) first; later a standalone binary (PyInstaller or Nuitka) bundled in platform-specific VSIX packages | Extension users shouldn't need to manage Python |

**Python-specific risks and mitigations**
- *Startup time* (imports can add hundreds of ms): lazy-import heavy modules; keep `--protocol stdio` mode lean, since VS Code starts it once per session anyway.
- *Distribution*: users need Python 3.12+ until the standalone binary exists; `cmcoder doctor` checks the environment.
- *Persistent shell for Bash*: `asyncio` subprocess with sentinel markers to detect command end and capture exit codes; Windows support later.

### Packages

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
├── vscode/                   # TypeScript extension + React webview
└── evals/                    # benchmark tasks, runner, mock model server
```

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
| No parallel tool calls | Set `parallel_tool_calls: false` and run calls one at a time |
| `content: null` with tool calls; empty assistant turns | Clean up before sending |
| HTTP 429 / 5xx / dropped stream | Retry with backoff and jitter, honouring `Retry-After`; resume the turn |
| Slow first token on a busy or cold remote server (model loading) | Separate connect, first-token and idle-stream timeouts; show "waiting for model" in the UI |

Later, optional adapters: OpenAI **Responses API**, native **Anthropic** (for prompt caching and extended thinking).

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
| `apiKey` / `apiKeyHelper` | `CMCODER_API_KEY` (falls back to `OPENAI_API_KEY`) | Static key, or a command that prints one |
| `headers` | `CMCODER_CUSTOM_HEADERS` | Extra HTTP headers (e.g. a gateway token) |
| `model` | `CMCODER_MODEL` | Main model; `/model` switches it in a session |
| `smallFastModel` | `CMCODER_SMALL_FAST_MODEL` | Used for titles, summaries, quick classification |
| `subagentModel` | — | Default model for subagents (falls back to `model`) |
| — | `CMCODER_DISABLE_NONESSENTIAL_TRAFFIC=1` | No update checks or anything other than model, WebFetch and MCP calls |

### 4.5 Network and transport (remote model)
- TLS verification always on; custom CA bundle via `caCertPath` / `SSL_CERT_FILE` for corporate or self-signed servers.
- `HTTPS_PROXY` / `NO_PROXY` respected; optional mTLS client certificate.
- Connection pooling and HTTP/2 keep-alive to cut per-turn latency.
- Plain `http://` to a non-localhost host prints a one-time warning, since code is sent in clear text.
- `cmcoder doctor` tests reachability, TLS, auth, the model list (`GET /models`), streaming and tool calling, and reports latency to first token.

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
- Unknown models get conservative defaults; `cmcoder doctor --probe` runs a short
  tool-calling test against the endpoint and suggests a profile.

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

- **Modes:** `default` (ask), `acceptEdits`, `plan` (read-only, then propose a plan), `bypassPermissions`. Shift+Tab cycles through them in the TUI.
- **Rules:** `allow` / `ask` / `deny` lists in settings, e.g. `Bash(npm test:*)`, `Edit(src/**)`, `WebFetch(domain:github.com)`, `mcp__server__tool`. Deny wins.
- **Interactive prompt:** allow once / allow always (saved to `settings.local.json`) / deny with a reason for the model.
- **Working directory boundary:** writes outside the project and added directories need approval.
- **Bash sandbox (opt-in, then default):** bubblewrap on Linux, Seatbelt on macOS; filesystem writes limited to the project, network limited to an allowlist.
- Commands are checked for injection tricks (e.g. `;`, `$(...)`, `&&` chains) before an allow rule is applied to them.

## 9. Context and memory

- **Memory files**, loaded at startup in this order:
  enterprise → `~/.cmcoder/CMCODER.md` → `CMCODER.md` in each folder from the repo root down to the current one → `CMCODER.local.md`.
  For compatibility, `AGENTS.md` is also read if present. `#` in the prompt adds a line to memory.
- **Auto-compaction** at a set share of the model profile's `contextWindow` (default 80%): the small/fast model summarises older turns; the recent turns and the todo list are kept as they are. `/compact [focus]` runs it by hand.
- **Tool-output limits:** long output is cut to head and tail, with the full text saved to a file the model can read if it needs to.
- `@file` mentions attach file contents; `/context` shows how the window is being used.

## 10. Sessions

- Saved as JSONL under `~/.cmcoder/projects/<project-hash>/<session-id>.jsonl`.
- `--continue`, `--resume [id]`, `/resume` picker.
- **Checkpoints:** file state saved before each edit; `/rewind` restores code, the conversation, or both.
- Cost and token use are tracked per session (`/cost`); prices per model are configurable and may be 0 for local models.

## 11. Configuration

Layered, with later layers overriding earlier ones:

1. Enterprise policy (`/etc/cmcoder/managed-settings.json`) — always wins
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
- Textual-based: streaming Markdown, tool-call cards, permission dialogs, diff previews, todo panel, Esc to interrupt, history, `!` to run a shell command directly.
- `cmcoder -p "…" [--output-format text|json|stream-json] [--max-turns N] [--allowedTools …]` for scripts and CI.

### 13.2 Agent Protocol (stdio)
`cmcoder --protocol stdio` reads and writes newline-delimited JSON:
- **Client → agent:** `user_message`, `interrupt`, `permission_response`, `set_mode`, `set_model`, `ide_context` (open file, selection, diagnostics), `ide_tool_result`.
- **Agent → client:** `assistant_delta`, `reasoning_delta`, `tool_call`, `tool_result`, `permission_request`, `ide_tool_request`, `todo_update`, `usage`, `turn_end`, `error`.

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

- `evals/tasks/*`: small repos, each with a task prompt and a check script (tests pass, file matches, etc.).
- Categories: read-only Q&A, single-file fix, multi-file refactor, run-tests-and-fix, tool-use hygiene (no blind overwrites, respects denials).
- Runner reports success rate, turns, tokens, tool-error rate and time, per model and profile.
- Runs in CI on every core change against a **mock OpenAI-compatible server** that replays recorded responses (no GPU or API key needed), and fully against real remote endpoints before releases.

## 16. Roadmap

| Phase | Scope | Done when |
|---|---|---|
| **0 — Foundations** | Python package skeleton (uv, ruff, pyright, pytest), protocol types, provider layer + OpenAI-compatible adapter (TLS, headers, retries), model profiles, agent loop, Read/Write/Edit/Glob/Grep/Bash, basic TUI, `-p`, `doctor`, mock server, first ~20 eval tasks | Fixes a simple bug end-to-end against a remote Ollama and one hosted OpenAI-compatible endpoint |
| **1 — Daily driver** | Permissions + rules, sessions/resume, memory files, auto-compaction, prompted-tool fallback and repair, edit-format variants, `doctor`, TodoWrite, checkpoints | Comfortable for daily use on a real repo |
| **2 — VS Code** | `--protocol stdio`, generated TS protocol types, extension, webview chat, native diffs, IDE context and tools | Same task behaves the same in CLI and VS Code |
| **3 — Extensibility** | MCP client, hooks, custom slash commands, subagents (`Task`) with per-role models, skills, Bash sandbox | Teams can customise it without forking |
| **4 — Hardening** | Responses API / Anthropic adapters, OpenTelemetry, enterprise policy, standalone binary + platform-specific VSIX, Windows support | Release candidate |

## 17. Open questions

1. Product name and command name (`cmcoder` is a placeholder).
2. Licence and distribution (internal only vs. public PyPI/Marketplace).
3. Windows support timing (sandbox and shell behaviour differ a lot).
4. Default web search backend (needs an API key, or off by default?).
5. Which 2–3 models are the reference targets for evals and default profiles.
6. Where the remote model server lives (cloud API, a GPU box on the LAN, a VPN host) and how it's secured: this decides the default auth and TLS guidance.
