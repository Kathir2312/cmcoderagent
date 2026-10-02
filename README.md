# cmcoder

An agentic coding assistant for the terminal (and, from Phase 2, VS Code), in the style of Claude Code,
that works with **any OpenAI-compatible endpoint**, such as a LiteLLM gateway serving Qwen3 models on your network.

The agent runs on your machine (it reads and edits files and runs commands there); the model always runs on a
remote server. See [docs/DESIGN.md](docs/DESIGN.md) for the architecture and roadmap, and
[docs/README.md](docs/README.md) for the per-phase plans and code guides (for Python developers and for
LangChain/LangGraph developers). Phase 0 is complete; Phase 1 is in progress ([plan](docs/phase1/PLAN.md)).

> Status: **Phase 0 complete, Phase 1 in progress** (Windows, macOS and Linux). Working agent loop, tools, permissions, LiteLLM/OpenAI-compatible provider,
> basic interactive terminal UI, headless mode, `doctor` and `login`.

## Install

Works on **Windows, macOS and Linux**. Requires Python 3.11+ and bash:

- **Windows:** install [Git for Windows](https://git-scm.com/download/win), which includes Git Bash. cmcoder
  finds it automatically (like Claude Code, it runs shell commands with Git Bash). If it's somewhere unusual,
  set `CMCODER_GIT_BASH_PATH` to the full path of `bash.exe`. Run cmcoder itself from PowerShell, Windows
  Terminal, cmd or the VS Code terminal.
- **macOS / Linux:** bash is already there.

[ripgrep](https://github.com/BurntSushi/ripgrep) (`rg`) is optional but recommended for large repos; without
it Grep/Glob use a slower built-in search. (`winget install BurntSushi.ripgrep.MSVC`, `brew install ripgrep`,
`apt install ripgrep`.)

```bash
uv tool install git+https://github.com/Kathir2312/cmcoderagent   # or: pipx install ...
```

For development: `uv sync`, then run it with `uv run cmcoder`.

## Configure

Create `~/.cmcoder/settings.json` (on Windows: `%USERPROFILE%\.cmcoder\settings.json`) (see [docs/settings.example.json](docs/settings.example.json)):

```json
{
  "providers": {
    "corp": { "baseUrl": "https://aiXIngerence.localnw.ae/v1" }
  },
  "model": "corp:qwen3-27b",
  "smallFastModel": "corp:qwen3-7b"
}
```

Model names are whatever your LiteLLM admin configured; `cmcoder models` lists them.

Then store your LiteLLM API key (kept in the OS keychain, never in settings files) and check everything:

```bash
cmcoder login
cmcoder doctor
```

`doctor` checks DNS (VPN), proxy settings, the TLS certificate chain, the API key, the model list, streaming,
the Qwen3 thinking switch and tool calling, and tells you what to fix.

**Internal certificates.** cmcoder trusts the OS certificate store (the Windows certificate store, macOS Keychain,
or the Linux CA bundle), so if IT has installed the company root CA you need nothing else. Otherwise set `"caCertPath": "/path/to/company-root-ca.pem"` on the provider (or
`CMCODER_CA_CERT`). Certificate verification is never switched off.

**Proxies.** `HTTPS_PROXY`/`NO_PROXY` are respected. If you use a corporate proxy, add `.localnw.ae` to `NO_PROXY`.

### Settings files

Later layers override earlier ones; permission rules from all layers are combined.

| File | Scope |
|---|---|
| `~/.cmcoder/settings.json` | you, all projects |
| `.cmcoder/settings.json` | the project (commit it) |
| `.cmcoder/settings.local.json` | you, this project (git-ignored; "always allow" answers go here) |
| `CMCODER_*` env vars | this shell |

Environment variables: `CMCODER_BASE_URL`, `CMCODER_API_KEY`, `CMCODER_MODEL`, `CMCODER_SMALL_FAST_MODEL`,
`CMCODER_CA_CERT`, `CMCODER_CUSTOM_HEADERS` (`"Name: value; Other: value"`), `CMCODER_CONFIG_DIR`,
`CMCODER_GIT_BASH_PATH` (Windows).
`OPENAI_BASE_URL` is used if `CMCODER_BASE_URL` isn't set; `OPENAI_API_KEY` is used only together with `OPENAI_BASE_URL`, so an
unrelated OpenAI key is never sent to your gateway. `CMCODER_API_KEY` overrides the key stored by `cmcoder login`.

### Project memory

cmcoder reads `CMCODER.md` (and `AGENTS.md`) from `~/.cmcoder/`, the project root and each folder down to the
current one, plus `CMCODER.local.md`, and follows them as instructions.

## Use

```bash
cmcoder                       # interactive session
cmcoder "explain src/app.py"  # interactive, starting with a prompt
cmcoder -p "fix the failing test" --permission-mode acceptEdits --allowedTools "Bash(pytest:*)"
git diff | cmcoder -p "review this diff"
cmcoder -p "..." --output-format json         # or stream-json (one event per line)
```

With `"smallFastModel"` set (e.g. your Qwen3 7B), side jobs go to the small model: compaction summaries and
conversation titles (shown by `/resume`).

Conversations are saved per project: `cmcoder -c` continues the latest one, `cmcoder -r <id>` resumes a
specific one, and `/resume` lists them (files under `~/.cmcoder/projects/`, owner-only, deleted after
`cleanupPeriodDays`, default 30; `"persistSessions": false` turns saving off).

`/rewind` goes back to one of your earlier messages and undoes the agent's file changes since then (Write/Edit;
not Bash commands), the conversation, or both.

For multi-step work the model keeps a todo list (TodoWrite), shown as a ☑ ◐ ☐ checklist; `/todos` shows it again.

`cmcoder --tui` (or `"ui": "textual"`) opens a full-screen UI with a status bar and a fixed-size permission dialog;
it's opt-in until it has been tried on Windows terminals (`/resume` and `/rewind` are classic-only for now).

In the interactive session: `/help`, `/model`, `/mode`, `/compact`, `/resume`, `/rewind`, `/todos`, `/clear`, `/cost`, `/exit`; Shift+Tab cycles the
permission mode; Ctrl+C interrupts the current turn.

### Permissions

| Mode | Behaviour |
|---|---|
| `default` | reads inside the project are allowed; edits and commands ask first |
| `acceptEdits` | file edits inside the project are allowed; commands still ask |
| `plan` | read-only; edits and non-read-only commands are refused |
| `bypassPermissions` | everything allowed except deny rules (use with care) |

Rules: `Read`, `Edit(src/**)` (also covers Write), `Bash(npm test:*)` (prefix), `Bash(git status)` (exact).
Deny rules win over allow rules. Prefix rules never approve chained commands (`;`, `&&`, `|`, `$( )`, redirects).
Files such as `.env`, `*.pem`, `*.key` and `secrets/` are never read (or shown in Grep results) unless an allow
rule names them. Editing `.cmcoder/`, `.git/` or `~/.cmcoder/` always asks, except in `bypassPermissions`.

High-risk commands (`rm -rf`, `git clean`, `git reset --hard`, force push, `sudo`, `curl … | sh`, `del /s`,
`Remove-Item -Recurse`, …) always ask, in every mode including `bypassPermissions`, and are never remembered or
approved by allow rules; `-p` runs refuse them. To block them entirely set
`"permissions": {"highRiskCommands": "deny"}` in `~/.cmcoder/settings.json`.

### File work goes through the file tools

Models like Qwen sometimes write files with `cat > f << EOF` or read them with `cat`/`grep` in Bash, which needs
your approval each time and skips cmcoder's checks. Such plain file work isn't run: the model is told which tool
to use instead (Write, Read, Edit, Grep, Glob), with no prompt for you. Pipelines and other real shell work run as
usual, and the model can send the same command again if it really needs the shell. Turn it off per model with
`"modelProfiles": [{"match": "qwen3*", "steerBashFileWork": false}]`.

### Managed settings (for administrators)

An organisation can enforce rules that users and repositories can't loosen, in an admin-only file:
`C:\Program Files\cmcoder\managed-settings.json` (Windows), `/Library/Application Support/cmcoder/managed-settings.json`
(macOS), `/etc/cmcoder/managed-settings.json` (Linux). It can disable `bypassPermissions`, deny high-risk commands,
add deny rules, allow only its own allow rules, lock cmcoder to the company gateway (`lockProviders`) and set
environment variables. If the file is broken, cmcoder won't start. See
[docs/managed-settings.example.json](docs/managed-settings.example.json); `cmcoder doctor` shows what's enforced.

### Context window

Qwen3 is often served with a 32K-token window, which an agent fills quickly. cmcoder sizes each request to the
room left and pages long files. When the conversation reaches 80% of the window it **summarises the older
part** (with `smallFastModel` if set) and keeps the recent messages; `/compact [what to keep]` does this on
demand, and `/clear` starts fresh. Settings: `"autoCompact": true`, `"autoCompactThreshold": 0.8`. If
summarising fails, the oldest tool outputs are dropped instead. cmcoder finds the real window by asking the
server once (and learns it from the server's error if a request is ever too long); `cmcoder doctor` shows the
value and where it came from. Set `"modelProfiles": [{"match": "qwen3*", "contextWindow": 40960}]` to override
it. If you can, have the gateway serve a longer context.

**Privacy:** each turn sends your prompt, file contents the agent reads and command output to the configured model
endpoint. LiteLLM admins may be able to see that traffic in gateway logs.

## Development

```bash
uv sync
uv run pytest -q                       # unit, CLI, TLS and terminal (pty, not on Windows) tests
uv run ruff check src tests evals && uv run pyright
uv run python evals/run.py --mock      # eval harness with scripted replies
LITELLM_BIN=/path/to/litellm uv run pytest tests/test_litellm_integration.py  # through a real LiteLLM proxy
uv run python evals/run.py             # evals against your real endpoint/model
uv run cmcoder protocol-schema         # Agent Protocol JSON Schema (for the VS Code extension)
```

`python -m cmcoder.testing.mock_server --script replies.json` runs a scripted OpenAI-compatible server for
manual testing.
