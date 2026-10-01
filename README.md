# cmcoder

An agentic coding assistant for the terminal (and, from Phase 2, VS Code), in the style of Claude Code,
that works with **any OpenAI-compatible endpoint**, such as a LiteLLM gateway serving Qwen3 models on your network.

The agent runs on your machine (it reads and edits files and runs commands there); the model always runs on a
remote server. See [docs/DESIGN.md](docs/DESIGN.md) for the architecture and roadmap.

> Status: **Phase 0**. Working agent loop, tools, permissions, LiteLLM/OpenAI-compatible provider,
> basic interactive terminal UI, headless mode, `doctor` and `login`.

## Install

Requires Python 3.11+, `bash` and [ripgrep](https://github.com/BurntSushi/ripgrep) (`rg`). Linux and macOS for now.

```bash
uv tool install git+https://github.com/Kathir2312/cmcoderagent   # or: pipx install ...
```

For development: `uv sync`, then run it with `uv run cmcoder`.

## Configure

Create `~/.cmcoder/settings.json` (see [docs/settings.example.json](docs/settings.example.json)):

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

**Internal certificates.** cmcoder trusts the OS certificate store, so if IT has installed the company root CA you
need nothing else. Otherwise set `"caCertPath": "/path/to/company-root-ca.pem"` on the provider (or
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
`CMCODER_CA_CERT`, `CMCODER_CUSTOM_HEADERS` (`"Name: value; Other: value"`), `CMCODER_CONFIG_DIR`.
`OPENAI_BASE_URL` / `OPENAI_API_KEY` are used as fallbacks.

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

In the interactive session: `/help`, `/model`, `/mode`, `/clear`, `/cost`, `/exit`; Shift+Tab cycles the
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
Files such as `.env`, `*.pem`, `*.key` and `secrets/**` are never read unless an allow rule names them.

**Privacy:** each turn sends your prompt, file contents the agent reads and command output to the configured model
endpoint. LiteLLM admins may be able to see that traffic in gateway logs.

## Development

```bash
uv sync
uv run pytest -q                       # unit, CLI, TLS and terminal (pty) tests
uv run ruff check src tests evals && uv run pyright
uv run python evals/run.py --mock      # eval harness with scripted replies
uv run python evals/run.py             # evals against your real endpoint/model
uv run cmcoder protocol-schema         # Agent Protocol JSON Schema (for the VS Code extension)
```

`python -m cmcoder.testing.mock_server --script replies.json` runs a scripted OpenAI-compatible server for
manual testing.
