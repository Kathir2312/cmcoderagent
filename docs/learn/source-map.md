# Source map: what lives where

cmcoder is one Python program (the agent) and thin front ends around it:
the terminal, and a plugin per IDE that starts the same program and shows a
shared chat page. This page says where each part lives and where to start
reading.

## How the parts talk

```
 you ──► front end ─────────────────────────► cmcoder (Python, src/cmcoder)
          terminal: cli/repl.py, cli/tui.py      │
          IDE plugin + chat page                 │  agent loop (core/agent.py)
            └ `cmcoder --protocol stdio`  ◄──────┤    ├─ tools (tools/)  ─► your files, Git Bash
              Agent Protocol: JSON lines         │    ├─ permissions, hooks, skills, subagents (core/)
              (protocol/)                        │    ├─ MCP servers (mcp_client.py)
                                                 │    └─ code search index (rag/)
                                                 ▼
                                   provider (providers/) ─► your AI gateway (LiteLLM, Open WebUI)
```

- The **agent** runs on the developer's PC: it reads and edits files and runs
  commands there. The **model** always runs on the gateway.
- Every IDE plugin starts the same program with `--protocol stdio` and relays
  the **Agent Protocol** (JSON events and messages, one per line) between it
  and the chat page. The plugins add only what needs the IDE: the editor
  context, diffs, opening files, settings.
- Developers' PCs have no Python: the program is built with PyInstaller and
  ships inside each package ([../build-package/](../build-package/README.md)).

## The repository

| Folder | What | Language |
|---|---|---|
| `src/cmcoder/` | the agent (below) | Python 3.11+ |
| `clients/web-panel/` | the chat page and the Agent Navigator, the same files in every IDE | TypeScript |
| `clients/jvm-core/` | the part of the Eclipse, NetBeans and JetBrains plugins that's the same for all: finding and starting cmcoder, the protocol, the host | Java 17 |
| `vscode/` | the VS Code extension | TypeScript |
| `visualstudio/` | the Visual Studio 2022 extension (`Cmcoder.Core`: the host; `Cmcoder.VisualStudio`: the IDE side) | C# |
| `eclipse/` | the Eclipse plugin, one fragment per platform, the update site (Maven/Tycho) | Java |
| `netbeans/` | the NetBeans plugin (Maven) | Java |
| `jetbrains/` | the plugin for IntelliJ IDEA and the other JetBrains IDEs (Gradle) | Java |
| `packaging/` | building the standalone program and every package; the release bundle; the one-command scripts | Python, PowerShell, bash |
| `branding/` | your company's name, icons, settings template, managed settings | files |
| `tests/` | the Python tests (each IDE's own tests are in its folder) | Python |
| `evals/` | the eval harness: scripted tasks, run against the mock server or a real model | Python |
| `docs/` | the documentation, by reader ([../README.md](../README.md)) | Markdown |

Each IDE folder has a `README.md` for its developers
([../../vscode/README.md](../../vscode/README.md),
[../../visualstudio/README.md](../../visualstudio/README.md),
[../../eclipse/README.md](../../eclipse/README.md),
[../../netbeans/README.md](../../netbeans/README.md),
[../../clients/jvm-core/README.md](../../clients/jvm-core/README.md),
[../../clients/web-panel/README.md](../../clients/web-panel/README.md)).

## Inside `src/cmcoder/`

| Folder or file | What |
|---|---|
| `cli/` | the command line: `main.py` (entry point and options), `factory.py` (builds providers and agents from settings), `repl.py` (the terminal), `tui.py` (the full-screen UI), `headless.py` (`-p`), `stdio.py` (`--protocol stdio`, for the IDEs), `doctor.py`, `setup_cmd.py`, `mcp_cmd.py`, `rag_cmd.py`, `agent_map.py` |
| `core/` | the agent: `agent.py` (the loop: model → tool calls → results → repeat), `prompt.py` (system prompt and `CMCODER.md`), `permissions.py`, `readonly.py`, `risk.py`, `hooks.py`, `commands.py`, `skills.py`, `subagents.py`, `critic.py`, `compaction.py`, `context.py`, `sessions.py`, `checkpoints.py` (`/rewind`), `vision.py`, `steer.py`, `ide.py`, `titles.py` |
| `tools/` | the tools the model calls: `files.py` (Read, Write, Edit), `search.py` (Glob, Grep), `bash.py` and `shell.py` (Bash, a persistent shell), `todo.py`, `code_search.py`, `registry.py` (the default set) |
| `providers/` | talking to the gateway: `openai_compat.py` (OpenAI Chat Completions), `openwebui.py`, `transport.py` (TLS, proxies, timeouts), `auth.py` (the API key in the OS keychain), `profiles.py` (what each model can do), `text_tools.py` and `thinking.py` (Qwen's text tool calls and `<think>`) |
| `protocol/` | the Agent Protocol: `events.py` (agent → front end), `messages.py` (front end → agent), `typescript.py` (generates `clients/web-panel/src/protocol.ts`) |
| `config/settings.py` | the settings layers, trust, managed settings |
| `rag/` | code search: `files.py` (what's indexed), `chunker.py`, `embed.py`, `index.py`, `stores.py` (local, Chroma), `setup.py` |
| `sandbox/` | the Bash sandbox: bubblewrap/`sandbox-exec`, `proxy.py` (the allowed hosts) |
| `mcp_client.py` | MCP servers: their tools, resources and prompts |
| `images.py` | attached images |
| `sensitive.py` | files never sent to the model (`.env`, keys) |
| `telemetry.py` | usage metrics (OpenTelemetry) |
| `brand.py`, `user_folder.py`, `shared_install.py`, `compat.py` | branding, `~/.cmcoder`, the one program in `~/.cmcoder/bin`, Windows vs POSIX |
| `testing/mock_server.py` | a scripted OpenAI-compatible server for tests and evals |

## Where to start reading

1. `cli/main.py`: what happens when you type `cmcoder`.
2. `cli/factory.py`: how settings become a provider and an agent.
3. `core/agent.py`: the loop. Everything else plugs into it.
4. `tools/registry.py` and `tools/files.py`: what a tool looks like.
5. `core/permissions.py`: how each tool call is allowed, asked or denied.
6. `providers/openai_compat.py`: what goes to the gateway and back.
7. `protocol/events.py`, then `cli/stdio.py`: how an IDE drives the agent.

The phase guides ([README.md](README.md)) walk through these in the order
they were built. Architecture decisions and why: [../project/DESIGN.md](../project/DESIGN.md).
