# cmcoder features: set up and connect

One page per feature: what it's for, how to set it up for yourself or for the
whole team, and how it works with the others. The formats are Claude Code's,
so existing examples work.

New to cmcoder? Install it first ([../guides/](../guides/README.md)) and do the
first-time setup ([../guides/setup.md](../guides/setup.md)).

## The features

| You want | Feature | Guide |
|---|---|---|
| Where every setting lives, and which one wins | settings files, environment variables | [settings.md](settings.md) |
| Your company's AI gateway, models, certificates, proxies | gateway and models (LiteLLM, Open WebUI) | [gateway.md](gateway.md), [openwebui.md](openwebui.md) |
| Find code by meaning in a big project | code search (RAG) | [code-search.md](code-search.md) |
| New tools: tickets, wikis, databases, internal APIs | MCP servers | [mcp.md](mcp.md) |
| Automatic checks: format after an edit, block a command | hooks | [hooks.md](hooks.md) |
| A shortcut for a prompt you type often | slash commands | [commands.md](commands.md) |
| Rules and context the agent always follows | project memory (`CMCODER.md`) | [memory.md](memory.md) |
| Know-how loaded only when it's needed | skills | [skills.md](skills.md) |
| Helpers for side jobs, or roles in a team | subagents | [agents.md](agents.md) |
| **A whole team: PM, developer, tester, DevOps, on one feature** | all of the above, together | [team.md](team.md) |
| What runs without asking, and what asks first | permissions, modes, trust | [permissions.md](permissions.md) |
| Commands that can't touch what they shouldn't | the Bash sandbox | [sandbox.md](sandbox.md) |
| A second check of each answer before you see it | the critic | [critic.md](critic.md) |
| Continue, resume, rewind; long conversations | sessions and context | [sessions.md](sessions.md) |
| Screenshots, error dialogs, diagrams in a message | images (and the vision helper) | [images.md](images.md) |
| Usage and cost numbers for your team | usage metrics (OpenTelemetry) | [telemetry.md](telemetry.md) |

## Yours, or the team's

Everything is files. Put them in your home folder for yourself, or in the
repository (committed) for everyone who works on it:

| Feature | Yours (every project) | The team's (in the repository) |
|---|---|---|
| Settings | `~/.cmcoder/settings.json` | `.cmcoder/settings.json` |
| MCP servers | `mcpServers` in `~/.cmcoder/settings.json`, or `cmcoder mcp add` | `.mcp.json` |
| Hooks | `hooks` in `~/.cmcoder/settings.json` | `hooks` in `.cmcoder/settings.json` |
| Slash commands | `~/.cmcoder/commands/<name>.md` | `.cmcoder/commands/<name>.md` |
| Project memory | `~/.cmcoder/CMCODER.md` | `CMCODER.md` (root and subfolders) |
| Skills | `~/.cmcoder/skills/<name>/SKILL.md` | `.cmcoder/skills/<name>/SKILL.md` |
| Subagents | `~/.cmcoder/agents/<name>.md` | `.cmcoder/agents/<name>.md` |
| Code search | `rag` in `~/.cmcoder/settings.json` | `rag` in `.cmcoder/settings.json` |

`~/.cmcoder` is `%USERPROFILE%\.cmcoder` on Windows. Yours win over a
repository's on a name clash. A ready-made team folder to copy:
[../examples/team/](../examples/team/README.md).

## What a repository needs your trust for

A repository you clone could contain any of these files, so the ones that
**run something or change permissions** are used only after you trust the
project, and each server or hook is approved once more before it first runs:

| From a repository | Untrusted | Trusted (`cmcoder trust`, or a workspace the IDE trusts) |
|---|---|---|
| MCP servers (`.mcp.json`) | ignored | each one asks once before it starts |
| hooks | ignored | each command asks once before it first runs |
| subagents | ignored | used |
| slash commands | used (they're prompts you run) | used, and their `allowed-tools` apply |
| skills, `CMCODER.md` | used (text only) | used |
| settings: `env`, allow rules, `acceptEdits`/`bypassPermissions` | ignored, with a warning | used |
| settings: `providers` (the gateway) | ignored | **ignored**: a repository can never receive your API key |

A repository's files must really be inside it (a symlink to `~/.ssh` is
ignored). How to trust a project in each IDE: [permissions.md](permissions.md#trusting-a-project).

## Checking it all

```bash
cmcoder doctor            # what's configured, where it's from, what's ignored and why
cmcoder trust             # what trusting this project would enable
cmcoder mcp list --check  # starts each MCP server and lists its tools
```

In the chat, `/agents` lists the agent types, `/mcp` the servers, `/help` the
commands. In Visual Studio, Eclipse, NetBeans and the JetBrains IDEs, **Copy
Diagnostics** includes the `doctor` report.

Administrators can set rules no one can loosen (managed settings):
[../admin/README.md](../admin/README.md#5-managed-settings-rules-developers-cant-change).
