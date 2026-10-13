# cmcoder

An agentic coding assistant in the style of Claude Code, for the **terminal**
and for **VS Code, Visual Studio, Eclipse, NetBeans, IntelliJ IDEA and the
other JetBrains IDEs**. It works with **any OpenAI-compatible endpoint**, such
as a LiteLLM gateway serving Qwen3 models on your network, or Open WebUI.

The agent runs on the developer's PC (it reads and edits files and runs
commands there, asking first); the model runs on your gateway. Developers'
PCs need **no Python**: every package carries its own standalone `cmcoder`.

> Status: **Phases 0–7 complete** (Windows, macOS and Linux): the terminal and
> TUI, VS Code, extensibility (MCP, hooks, skills, subagents), hardening
> (sandbox, standalone build, both gateways), code search (RAG), Visual Studio,
> Eclipse and NetBeans, and IntelliJ IDEA and the other JetBrains IDEs.
> History: [docs/project/](docs/project/README.md).

## Start here

| You are | Start with |
|---|---|
| **A developer using cmcoder** (install, set up, use it in your IDE or terminal) | [docs/guides/](docs/guides/README.md) |
| **A team lead** (code search, MCP, hooks, skills, subagents, a whole agent team) | [docs/features/](docs/features/README.md), then [the team setup](docs/features/team.md) |
| **An administrator** (company settings, managed rules, branding, roll-out) | [docs/admin/](docs/admin/README.md) |
| **A release engineer** (build and pack each package with cmcoder inside) | [docs/build-package/](docs/build-package/README.md) |
| **A new developer on cmcoder** (learn the code, run the tests) | [docs/learn/](docs/learn/README.md) |

All the documentation, by reader: [docs/](docs/README.md).

## Install

Use the files from the release bundle (or GitHub → **Actions → Release build**
→ the latest green run → Artifacts). Each contains its own `cmcoder`; on
Windows, Git for Windows is the only other need.

| You work in | File | Guide |
|---|---|---|
| A terminal | `cmcoder-terminal-<platform>.zip`: unzip, run `install.cmd` (Windows) or `sh install.sh` | [terminal](docs/guides/terminal.md) |
| VS Code | `cmcoder-<platform>.vsix` | [VS Code](docs/guides/vscode.md) |
| Visual Studio 2022 | `cmcoder-visualstudio-win32-x64.vsix` | [Visual Studio](docs/guides/visualstudio.md) |
| Eclipse | `cmcoder-eclipse.zip` (every platform) | [Eclipse](docs/guides/eclipse.md) |
| NetBeans | `cmcoder-netbeans-<platform>.nbm` | [NetBeans](docs/guides/netbeans.md) |
| IntelliJ IDEA, PyCharm, Rider, … | `cmcoder-jetbrains-<platform>.zip` | [JetBrains IDEs](docs/guides/intellij.md) |

Then, once: `cmcoder setup` (your gateway and API key; the key goes to the OS
keychain, never a file) and `cmcoder doctor` ([setup](docs/guides/setup.md)).

From source (contributors; Python 3.11+ through uv): `uv sync`, then
`uv run cmcoder` ([contributing](docs/learn/contributing.md)).

## Features

| | |
|---|---|
| [Gateway and models](docs/features/gateway.md) | LiteLLM, Open WebUI, any OpenAI-compatible server; company certificates and proxies; a small model for side jobs, a vision model for images |
| [Code search (RAG)](docs/features/code-search.md) | find code by meaning; a built-in index or Chroma |
| [MCP servers](docs/features/mcp.md) | tickets, wikis, databases and internal APIs as tools |
| [Hooks](docs/features/hooks.md) | your checks on agent events: format after an edit, block a command |
| [Slash commands](docs/features/commands.md), [memory](docs/features/memory.md), [skills](docs/features/skills.md) | your prompts, your rules (`CMCODER.md`), your know-how |
| [Subagents](docs/features/agents.md) and [the team](docs/features/team.md) | helpers in parallel; a PM, developer, tester and DevOps on one feature |
| [Permissions](docs/features/permissions.md) and [the sandbox](docs/features/sandbox.md) | modes, rules, trust, high-risk commands; commands that can't reach what they shouldn't |
| [Critic](docs/features/critic.md), [sessions](docs/features/sessions.md), [images](docs/features/images.md) | a second check of answers; resume and rewind; screenshots in a message |
| [Settings](docs/features/settings.md), [usage metrics](docs/features/telemetry.md) | where everything is configured; OpenTelemetry for the team |

## The repository

| Folder | What |
|---|---|
| `src/cmcoder/` | the agent (Python) |
| `clients/` | the shared chat page (TypeScript) and the JVM core of the Java plugins |
| `vscode/`, `visualstudio/`, `eclipse/`, `netbeans/`, `jetbrains/` | the IDE plugins |
| `packaging/` | the standalone build, every package, the release bundle, the one-command scripts |
| `branding/` | your company's name, icons, settings template and managed settings |
| `tests/`, `evals/` | the tests and the eval harness |
| `docs/` | the documentation, by reader |

What each part does and where to start reading: [docs/learn/source-map.md](docs/learn/source-map.md).

## Development

```bash
uv sync
uv run ruff check src tests evals packaging && uv run pyright
uv run pytest -q
uv run python evals/run.py --mock
```

Everything else (the IDE parts, the integration tests, CI, the rules that
always hold): [docs/learn/contributing.md](docs/learn/contributing.md).
