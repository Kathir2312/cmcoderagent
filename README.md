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

**Without Python:** the "Release build" workflow (GitHub → Actions) builds a standalone `cmcoder` for Windows x64,
Linux x64 and macOS arm64 (`cmcoder-<platform>`: a folder with the program; put it on PATH) and a VS Code extension
per platform with that `cmcoder` inside (`cmcoder-<platform>.vsix`): install it and the extension needs nothing
else. It uses the bundled `cmcoder` unless you set `cmcoder.executable`. Build them yourself with
`uv run --with pyinstaller python packaging/build.py`, then `uv run python packaging/vsix.py` (Node.js 22).

**Your company's icon and name:** replace the files in `branding/` before building (icon, side-bar icon, name,
VS Code publisher, colour, text logo); `cmcoder terminal-profile` adds a Windows Terminal profile with them. See
[docs/phase4/branding.md](docs/phase4/branding.md).

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

**Open WebUI instead of (or as well as) LiteLLM.** Give the provider `"type": "openwebui"` and the Open WebUI
address; the key is an Open WebUI API key (Settings → Account → API keys). Models served by Ollama behind Open WebUI
get their context window set by cmcoder on every request. Details: [docs/phase4/openwebui.md](docs/phase4/openwebui.md).

```json
{ "providers": { "webui": { "type": "openwebui", "baseUrl": "https://chat.example.com" } },
  "model": "webui:qwen3:32b" }
```

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

### Project settings and trust

A project's `.cmcoder/settings.json` (and `settings.local.json`, which a repository can commit too) comes from
whoever wrote the repository, so some of it is only used once you trust the project:

| In a project's settings | Untrusted project | Trusted project |
|---|---|---|
| `providers` (gateway URL, CA, headers) | ignored | **ignored**: gateways only come from your user or managed settings, so a repository can never receive your API key |
| `env`, `permissions.allow`, `defaultMode` `acceptEdits` / `bypassPermissions` | ignored, with a warning | used |
| everything else (deny rules, model, `maxTurns`, ...) | used | used |

Trust a project with `cmcoder trust` in its folder (it lists what it would enable; `cmcoder trust --revoke` undoes
it), or for one run with `--trust-project`. "Always allow" answers cmcoder saves itself stay trusted as long as
nobody else changes `settings.local.json`. The VS Code extension passes `--trust-project` only for workspaces VS
Code itself trusts. `cmcoder doctor` shows the project's trust state and anything ignored.

Examples of everything below, and what a repository needs your trust for:
[docs/phase3/customising.md](docs/phase3/customising.md).

### MCP servers

[MCP](https://modelcontextprotocol.io) servers give cmcoder more tools (issue trackers, databases, internal APIs):

```bash
cmcoder mcp add github --url https://mcp.example.com/github -H "Authorization: Bearer ${GITHUB_TOKEN}"
cmcoder mcp add files -- npx -y @modelcontextprotocol/server-filesystem C:\docs
cmcoder mcp list --check      # start them and list their tools
```

They are saved under `mcpServers` in `~/.cmcoder/settings.json`; `${VAR}` is read from the environment, so tokens
stay out of the file. Their tools are named `mcp__<server>__<tool>` and ask before running, unless an allow rule says
otherwise (`"mcp__github"` for all of a server's tools, `"mcp__github__create_issue"` for one). `/mcp` shows their
status; a server's own output goes to `~/.cmcoder/logs/mcp-<name>.log`.

A project can list servers in `.mcp.json` (Claude Code's format). They are used only in a trusted project, and each
one asks once before it first starts (`cmcoder mcp approve NAME` approves it ahead of time); a changed command asks
again. Administrators can restrict servers with `allowedMcpServers` / `deniedMcpServers` in the managed settings.

### Hooks

Hooks run your own commands on agent events, e.g. a linter after every edit or a check that blocks risky commands
(same format as Claude Code):

```json
{
  "hooks": {
    "PostToolUse": [{"matcher": "Edit|Write", "hooks": [{"type": "command", "command": "ruff check --quiet ."}]}],
    "PreToolUse":  [{"matcher": "Bash", "hooks": [{"type": "command", "command": "./scripts/check-command.sh"}]}]
  }
}
```

Events: `PreToolUse`, `PostToolUse`, `UserPromptSubmit`, `SessionStart`, `Stop`, `SubagentStop`, `PreCompact`,
`Notification`. A hook gets the event as JSON on stdin and runs in the project folder with the
Bash tool's shell (Git Bash on Windows), with `CMCODER_PROJECT_DIR` set. **Exit 2** blocks (stderr tells the model
why: the tool doesn't run, the prompt isn't sent, or the model keeps working after `Stop`); other non-zero exits
are warnings. JSON on stdout can decide too: `{"hookSpecificOutput": {"permissionDecision": "allow"|"ask"|"deny"}}`
for `PreToolUse` (an "allow" skips a normal prompt but never a deny rule or a high-risk command), and plain
stdout from `UserPromptSubmit`/`SessionStart` is added as context. A project's hooks are used only in a trusted
project and ask once before they first run; administrators can set `allowManagedHooksOnly`.

### Custom slash commands

A Markdown file is a command: `~/.cmcoder/commands/review.md` is `/review` in every project,
`.cmcoder/commands/review.md` only in this one (yours win on a name clash), and subfolders are namespaces
(`.cmcoder/commands/db/migrate.md` is `/db:migrate`). Same format as Claude Code:

```markdown
---
description: Review a file for bugs
argument-hint: <file>
allowed-tools: Read, Grep, Bash(git log:*)
---
Review $1 for bugs, then suggest tests. Extra notes: $ARGUMENTS
```

`$ARGUMENTS` is everything after the command and `$1`..`$9` its words (quotes group words); with no placeholder the
arguments are added at the end. `allowed-tools` are allow rules for that one turn (a repository's commands get
them only in a trusted project, and never with managed-only rules). A command only becomes a prompt: it never
runs anything by itself. MCP servers' prompts are commands too: `/mcp__<server>__<prompt> args`. Commands are
listed in `/help` and completed as you type in the terminal, the TUI and the VS Code panel (`/compact` works
there too).

### Subagents

The model can hand a self-contained job to a **subagent** with the `Task` tool: a fresh conversation with its own
tools (and possibly a smaller model) that returns one report, so searches and side quests don't fill the main
conversation. Built in: `general-purpose` (every tool, the main model) and `explore` (read-only Read/Glob/Grep, on
`subagentModel`, else `smallFastModel`, else the main model). Your own go in `~/.cmcoder/agents/*.md`, and a
trusted project's in `.cmcoder/agents/*.md` (same format as Claude Code):

```markdown
---
name: reviewer
description: Reviews a change for bugs. Use after editing code.
tools: Read, Grep, Glob, Bash
model: small
---
You are a careful reviewer. Report problems with file:line references.
```

`tools` limits the tools (default: all; `mcp__server` means all of a server's tools); `model` is `inherit`
(default), `small` or a model name. A subagent asks permission like the main agent (same rules and mode), runs
`PreToolUse`/`PostToolUse` hooks and `SubagentStop` when it finishes, can't start subagents itself, and its file
changes can be undone with `/rewind`. Its steps are shown inside the Task call in every front end.

Several Task calls in one reply **run at the same time** ("one subagent per project"): up to
`maxParallelSubagents` (default 4; 1 runs them one after another). Their permission questions come one at a time,
and with `-v` their step lines in the terminal are tagged with the task (`[Analyse TW.Core] ● Read(...)`). This needs a model that
sends several tool calls in one reply: on by default for Qwen3 models, otherwise `"parallelToolCalls": true` in
`modelProfiles`. A subagent may make `subagentMaxTurns` model calls (default 100, separate from `maxTurns`); it's
told when 5 are left, and at the limit it makes one last call without tools to write its report, so the main agent
always gets what it found (marked as possibly incomplete).

**The agent map** (Claude Code style) shows the turn's subagents while they work: state (queued, running,
waiting for permission, stopping, done, stopped, failed), steps used of its limit, tool calls, tokens, time, and
what each is doing now.

```
├─ ◐ 1. Analyse TW.Core  explore · 14/100 steps · 22 tools · 31k tokens · 1m12s
│     └ Read(Controllers/OrderController.cs)
├─ ⏸ 2. Analyse TW.Api  waiting for permission · general-purpose · 6/100 steps · …
└─ ○ 3. Analyse TW.Web  explore · queued
```

In the terminal it sits under the spinner (every step with `-v`), in the TUI above the input, in VS Code above the
input with a **Stop** button per subagent (and a status line on each Task card). **Stopping one subagent** leaves
the turn and the others running: it writes its report from what it has so far (a queued one simply doesn't start).
Terminal: Ctrl+C while subagents run asks which one to stop (`a` interrupts everything, as before); TUI:
`/agents stop <n>`. `/agents` lists the agent types and this session's runs; `/agents <n>` shows a run's steps
and report. Protocol: `subagent_status` events and the `stop_subagent` message.

### Skills

A skill is a folder with a `SKILL.md`: instructions for one kind of task, plus any templates, references or
scripts it needs. Yours go in `~/.cmcoder/skills/<name>/`, a project's in `.cmcoder/skills/<name>/` (yours win on a
name clash). Same format as Claude Code:

```markdown
---
name: release-notes
description: Writes release notes in our format. Use when asked for release notes or a changelog.
---
1. List the merged changes with `git log --oneline <last tag>..HEAD`.
2. Fill in template.md, grouping changes by area.
```

Only the names and descriptions are in the system prompt; when a task matches, the model loads the instructions
with the `Skill` tool, and the skill's other files with `Skill(skill, file)`. A skill is only text: it runs
nothing by itself and grants no permissions (scripts it mentions go through the Bash tool's permission checks).

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

For multi-step work the model keeps a todo list (TodoWrite), shown as a ☑ ► ☐ checklist; `/todos` shows it again.

`cmcoder --tui` (or `"ui": "textual"`) opens a full-screen UI with a status bar and a fixed-size permission dialog;
it's opt-in until it has been tried on Windows terminals (`/resume` and `/rewind` are classic-only for now).

**Symbols on Windows.** The classic Windows console (PowerShell or cmd in their own window) shows `?` for
characters its font lacks; with a raster font that is anything outside the console's code page (437, 850, ...):
the spinner, but also ● … ☐ ✓ ◐ ⚠. There cmcoder uses a basic set: an ASCII spinner (`- \ | /`), marks taken from
the code page (» √ ■ · and the box lines in 437, ASCII otherwise), and every other missing character turned into
an ASCII stand-in (`…` → `...`, `●` → `*`, `☐` → `[ ]`). It's chosen automatically; Windows Terminal, the VS Code
terminal, ConEmu and Git Bash get the full set. Override with `"symbols": "basic"` or `"unicode"` in settings (or
`CMCODER_SYMBOLS`); `cmcoder doctor` says which set is in use. Windows Terminal
(`winget install Microsoft.WindowsTerminal`) shows everything.

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

### Bash sandbox

On Linux, WSL2 and macOS the agent's Bash commands run in a **sandbox** (bubblewrap on Linux/WSL2, `sandbox-exec` on
macOS): they can write only inside the project and a temp folder, `.git` (all of it), `.cmcoder`, `.vscode`, `.idea`
and `.mcp.json` stay read-only, credential folders (`~/.ssh`, `~/.aws`, ...) and local services (Docker, the desktop
session) are out of reach, and the network is reachable only through cmcoder's proxy, which allows the hosts you
list. Git commands that read (`status`, `diff`, `log`) work in the sandbox; ones that write (`commit`, `checkout`,
`stash`) are run outside it, with your approval. Sandboxed commands don't ask for permission; deny rules,
high-risk commands and plan mode work as before. If a command really needs more, the model can ask to run it outside
the sandbox: that always asks you.

```json
"sandbox": {
  "network": { "allowedHosts": ["pypi.org", "*.pythonhosted.org", "registry.npmjs.org", "github.com"] },
  "writablePaths": ["~/.m2"],
  "autoAllow": true,
  "allowUnsandboxedCommands": true
}
```

`"enabled": false` (or `CMCODER_SANDBOX=off`) turns it off; a repository's settings can't change any of this unless
the project is trusted; managed settings can force it. On Linux/WSL2 it needs `bubblewrap`
(`sudo apt install bubblewrap`). Native Windows has no sandbox: run cmcoder inside WSL2 to get one there.
`cmcoder doctor` shows whether it is active.
On macOS, commands can connect to local ports only through cmcoder's proxy; `"network": {"allowLocalhost": true}`
lets them reach any local port (e.g. a test server they start), including services outside the sandbox.

### Code search (Phase 5)

An index of the project's code lets the model find code **by meaning** ("where do we retry failed payments?"): a
`CodeSearch` tool, and the best-matching code added to your messages automatically. It needs an embedding model on
your gateway (LiteLLM or Open WebUI) and works in the terminal and VS Code:

```
cmcoder rag setup        # embedding model, where the index lives, your settings or the project's; index now
cmcoder index            # bring it up to date (changed files only); --status, --rebuild, --clear
```

Inside a session: `/index` and `/index status`. VS Code: "cmcoder: Set Up Code Search", and the status bar item. The
index stays current by itself; secret files are never indexed. The index can live on this machine (built in), in
Chroma on this machine, or on a shared Chroma server. See [docs/phase5/code-search.md](docs/phase5/code-search.md).

### Usage metrics (OpenTelemetry)

Off by default. An administrator (managed settings) or you can send usage metrics to an OpenTelemetry collector
over OTLP/HTTP:

```json
"telemetry": {
  "enabled": true,
  "endpoint": "https://otel.corp.example:4318",
  "headers": { "Authorization": "Bearer ${OTEL_TOKEN}" },
  "resourceAttributes": { "team": "payments", "user.name": "${USERNAME}" }
}
```

Metrics: sessions (by front end), tokens and cost per model, tool calls by tool and result (MCP tools by server),
model errors, compactions, turns and their duration. **Only counts and timings**: never prompts, replies, code, file
paths or command text. The standard `OTEL_EXPORTER_OTLP_ENDPOINT` / `_HEADERS`, `OTEL_RESOURCE_ATTRIBUTES` and
`CMCODER_TELEMETRY=1` work too; a repository's settings can't turn it on or point it elsewhere unless the project is
trusted. `cmcoder doctor` checks the collector. Details: `src/cmcoder/telemetry.py`.

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

## VS Code extension (Phase 2)

The extension in [`vscode/`](vscode/) runs the same `cmcoder` (`cmcoder --protocol stdio`) behind a chat panel in
the side bar. Build and install it (needs Node.js 22+ to build; using it needs only VS Code and `cmcoder`):

```bash
cd vscode
npm ci
npm run package                                  # -> vscode/cmcoder.vsix
code --install-extension cmcoder.vsix
```

CI also builds `cmcoder.vsix` (the `cmcoder-vsix` artifact of the "VS Code extension" job). See
[vscode/README.md](vscode/README.md) for how to use it.

While it works, the panel shows a progress line above the input, as Claude Code does: an animated ✻ (in the theme's blue), what it's
doing ("Considering…", "Thinking…", "Running… Bash(dotnet build)", "Waiting for your answer…"), the time, the tokens
it has written this turn, and "Esc to interrupt". You can keep typing: messages sent meanwhile are queued (shown
above the input, × to drop one) and go out together when the turn ends; if you interrupt, they come back into
the input to edit.

## Development

```bash
uv sync
uv run pytest -q                       # unit, CLI, TLS and terminal (pty, not on Windows) tests
uv run ruff check src tests evals && uv run pyright
uv run python evals/run.py --mock      # eval harness with scripted replies (20 tasks)
LITELLM_BIN=/path/to/litellm uv run pytest tests/test_litellm_integration.py  # through a real LiteLLM proxy
uv run python evals/run.py             # evals against your real endpoint/model
uv run cmcoder protocol-schema         # Agent Protocol JSON Schema (for the VS Code extension)
uv run cmcoder protocol-schema --typescript > vscode/src/protocol.ts   # after changing the protocol
cd vscode && npm ci && npm run typecheck && uv run --project .. npm test   # the extension's tests
#   (on Windows: uv run --project .. npm.cmd test, or set CMCODER_TEST_PYTHON to cmcoder's python.exe)
```

`python -m cmcoder.testing.mock_server --script replies.json` runs a scripted OpenAI-compatible server for
manual testing.
