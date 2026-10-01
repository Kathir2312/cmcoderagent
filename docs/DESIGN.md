# cmcoderagent — Design

Status: **Draft for discussion** · Scope: architecture and roadmap only (no code yet)

cmcoderagent is a local, agentic coding assistant that runs on the developer's
machine. Its behaviour and user experience mirror Claude Code (CLI and VS Code
extension), but it talks to **any OpenAI-compatible model endpoint**, including
local Ollama, vLLM, LM Studio and llama.cpp servers.

---

## 1. Decisions so far

| # | Question | Decision |
|---|---|---|
| D1 | Build on the Claude Agent SDK, or our own engine? | **Our own engine** with a provider layer. The SDK ties us to Claude models. |
| D2 | Model targets / hardware | **Mirror Claude Code**: no fixed hardware assumption. The model is a setting (`model`, `/model`, env var) and a small/fast model can be set separately. |
| D3 | Local-only or mixed? | **Mirror Claude Code**: online by default with any OpenAI-compatible endpoint, cloud or local. No telemetry backend; a single switch disables all non-essential traffic. |
| D4 | Primary wire protocol | **OpenAI Chat Completions.** Ollama is reached through its OpenAI-compatible `/v1` endpoint. |
| D5 | Shared/team model server | **Out of scope for now.** Single user, endpoint on localhost or a URL the user configures. |
| D6 | Language / stack | TypeScript monorepo (Node ≥ 20). The VS Code extension is TS, and one language keeps the core shared. |
| D7 | How the IDE uses the agent | The VS Code extension **runs the CLI as a child process** and talks to it over JSON over stdio, as Claude Code does. There is one engine. |

## 2. Goals and non-goals

**Goals**
- Match Claude Code's core user experience: interactive TUI, `-p` headless mode,
  permission prompts, sessions/resume, project memory file, slash commands,
  MCP, hooks, subagents, and a VS Code extension with diff review.
- Work well with OpenAI-compatible servers, including mid-sized local models.
- Never send code anywhere except the configured model endpoint and tools the
  user approves (WebFetch, MCP servers).

**Non-goals (for now)**
- Multi-user servers, auth, rate limiting (D5).
- JetBrains or other IDEs (the protocol is kept IDE-neutral so they can be added later).
- Hosted or cloud execution.

## 3. Architecture

```
┌──────────────┐   ┌───────────────────┐   ┌──────────────┐
│  CLI / TUI   │   │ VS Code extension │   │ Headless -p  │
│   (Ink)      │   │ (webview + diffs) │   │ (json/stream)│
└──────┬───────┘   └─────────┬─────────┘   └──────┬───────┘
       │      Agent Protocol: JSON lines over stdio      │
       └──────────────┬──────┴─────────────────────────┘
               ┌──────▼───────┐
               │  Agent Core  │  loop · tools · permissions · context
               └──────┬───────┘
  ┌──────────┬────────┼─────────┬───────────┬──────────┐
Provider   Model    MCP      Hooks      Sessions    Config
(OpenAI-   profiles client   runner     (JSONL)     (layered)
 compat)
```

### Packages

| Package | Contents |
|---|---|
| `packages/protocol` | Agent Protocol message types (zod schemas) shared by every front end |
| `packages/core` | Agent loop, tools, permissions, context management, sessions, MCP, hooks |
| `packages/providers` | Internal message format, OpenAI-compatible adapter, model profiles |
| `packages/cli` | `cmcoder` binary: TUI, headless mode, `--protocol` stdio server mode |
| `packages/vscode` | Extension: runs the CLI process, webview chat, diff view, IDE tools |
| `evals/` | Benchmark tasks and runner for comparing models and catching regressions |

## 4. Provider layer (OpenAI-compatible first)

### 4.1 Internal message format
The core only ever sees provider-neutral types:

- `system` · `user` (text, images) · `assistant` (text, `reasoning`, `tool_calls[]`) · `tool` (call id, result, is_error)
- Stream events: `text_delta`, `reasoning_delta`, `tool_call_start/delta/end`, `usage`, `stop(reason)`

### 4.2 OpenAI Chat Completions adapter
Uses `POST {baseUrl}/chat/completions` with `stream: true`, `tools`, `tool_choice`,
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
| HTTP 429 / 5xx / dropped stream | Retry with backoff and jitter; resume the turn |

Later, optional adapters: OpenAI **Responses API**, native **Anthropic** (for prompt caching and extended thinking).

### 4.3 Ollama notes
- Reached at `http://localhost:11434/v1`. The API key is ignored but required by
  some clients, so it defaults to a placeholder.
- **Context window:** the OpenAI-compatible endpoint does not take Ollama's
  `num_ctx`, and Ollama's default window is small enough that the system prompt
  plus tools can be cut off without warning. Mitigations:
  1. `cmcoder doctor` checks the effective context size via `/api/show` and warns.
  2. Documentation: set `OLLAMA_CONTEXT_LENGTH` or create a custom model with a larger `num_ctx`.
  3. Optional later: a native `/api/chat` adapter that sets `num_ctx` on each request.
  *(Check the current Ollama defaults and options when we implement this.)*

### 4.4 Endpoint configuration (Claude Code-style)
| Setting | Env var | Meaning |
|---|---|---|
| `baseUrl` | `CMCODER_BASE_URL` (falls back to `OPENAI_BASE_URL`) | OpenAI-compatible endpoint |
| `apiKey` / `apiKeyHelper` | `CMCODER_API_KEY` (falls back to `OPENAI_API_KEY`) | Static key, or a command that prints one |
| `model` | `CMCODER_MODEL` | Main model; `/model` switches it in a session |
| `smallFastModel` | `CMCODER_SMALL_FAST_MODEL` | Used for titles, summaries, quick classification |
| `subagentModel` | — | Default model for subagents (falls back to `model`) |
| — | `CMCODER_DISABLE_NONESSENTIAL_TRAFFIC=1` | No update checks or anything other than model, WebFetch and MCP calls |

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
- **Repair:** fix malformed JSON automatically, then validate with zod; on failure,
  send a short, specific error back to the model (at most N retries per call).
- **Loop detection:** stop identical repeated calls and tell the model.
- **Keep the prompt prefix stable** so server-side KV/prefix caches (vLLM, llama.cpp, Ollama) are reused.

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
- Ink-based: streaming Markdown, tool-call cards, permission dialogs, diff previews, todo panel, Esc to interrupt, history, `!` to run a shell command directly.
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

- Code and prompts go **only** to the configured model endpoint, plus WebFetch/WebSearch and MCP servers the user has approved.
- No telemetry backend. Optional OpenTelemetry export (metrics/logs) to an endpoint the user chooses, off by default.
- `CMCODER_DISABLE_NONESSENTIAL_TRAFFIC=1` turns off update checks.
- API keys are read from env vars, `apiKeyHelper`, or the OS keychain, never written to session logs.

## 15. Evaluation

Started early, because quality depends heavily on the model:

- `evals/tasks/*`: small repos, each with a task prompt and a check script (tests pass, file matches, etc.).
- Categories: read-only Q&A, single-file fix, multi-file refactor, run-tests-and-fix, tool-use hygiene (no blind overwrites, respects denials).
- Runner reports success rate, turns, tokens, tool-error rate and time, per model and profile.
- Runs in CI on every core change (with a small local model or a mock), and runs fully before releases.

## 16. Roadmap

| Phase | Scope | Done when |
|---|---|---|
| **0 — Foundations** | Monorepo, protocol types, provider layer + OpenAI-compatible adapter, model profiles, agent loop, Read/Write/Edit/Glob/Grep/Bash, basic TUI, `-p`, first ~20 eval tasks | Fixes a simple bug end-to-end against Ollama and one hosted OpenAI-compatible endpoint |
| **1 — Daily driver** | Permissions + rules, sessions/resume, memory files, auto-compaction, prompted-tool fallback and repair, edit-format variants, `doctor`, TodoWrite, checkpoints | Comfortable for daily use on a real repo |
| **2 — VS Code** | `--protocol stdio`, extension, webview chat, native diffs, IDE context and tools | Same task behaves the same in CLI and VS Code |
| **3 — Extensibility** | MCP client, hooks, custom slash commands, subagents (`Task`) with per-role models, skills, Bash sandbox | Teams can customise it without forking |
| **4 — Hardening** | Responses API / Anthropic adapters, OpenTelemetry, enterprise policy, packaging (npm, VSIX), Windows support | Release candidate |

## 17. Open questions

1. Product name and command name (`cmcoder` is a placeholder).
2. Licence and distribution (internal only vs. public npm/Marketplace).
3. Windows support timing (sandbox and shell behaviour differ a lot).
4. Default web search backend (needs an API key, or off by default?).
5. Which 2–3 models are the reference targets for evals and default profiles.
