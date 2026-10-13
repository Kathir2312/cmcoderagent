# Settings: where they live and which one wins

cmcoder's settings are JSON files, in the same layers as Claude Code's. The
common ones, with examples, are in
[../examples/settings.example.json](../examples/settings.example.json) (a copy
is put in `~/.cmcoder/` the first time cmcoder starts); the table at the end
of this page links each setting to its guide.

## The layers

Later layers override earlier ones; permission rules from every layer are
combined (and deny rules always win).

| File | Scope | Shared? |
|---|---|---|
| `~/.cmcoder/settings.json` | you, every project | no |
| `.cmcoder/settings.json` | the project | yes: commit it |
| `.cmcoder/settings.local.json` | you, this project ("always allow" answers are saved here) | no: git-ignored |
| `CMCODER_*` environment variables | this shell | no |
| managed settings (installed by IT) | everyone on the PC; can't be loosened | [../admin/README.md](../admin/README.md#5-managed-settings-rules-developers-cant-change) |

`~/.cmcoder` is `%USERPROFILE%\.cmcoder` on Windows (`CMCODER_CONFIG_DIR`
moves it). The terminal and every IDE read the same files.

## A project's settings and trust

A project's `.cmcoder/settings.json` (and a committed `settings.local.json`)
comes from whoever wrote the repository, so some of it is used only once you
trust the project:

| In a project's settings | Untrusted project | Trusted project |
|---|---|---|
| `providers` (gateway URL, CA, headers) | ignored | **ignored**: gateways come only from your user or managed settings, so a repository can never receive your API key |
| `env`, `permissions.allow`, `defaultMode` `acceptEdits` / `bypassPermissions` | ignored, with a warning | used |
| everything else (deny rules, model, `maxTurns`, ...) | used | used |

Trusting a project: [permissions.md](permissions.md#trusting-a-project).
"Always allow" answers cmcoder saves itself stay trusted as long as nobody
else changes `settings.local.json`. `cmcoder doctor` shows the project's
trust state and anything ignored.

## Environment variables

| Variable | For |
|---|---|
| `CMCODER_BASE_URL`, `CMCODER_API_KEY`, `CMCODER_MODEL`, `CMCODER_SMALL_FAST_MODEL` | the gateway and models, for one shell ([gateway.md](gateway.md)) |
| `CMCODER_PROVIDER_TYPE` | `openwebui` for an Open WebUI gateway |
| `CMCODER_CA_CERT` | the company root certificate (PEM) |
| `CMCODER_CUSTOM_HEADERS` | extra headers to the gateway: `"Name: value; Other: value"` |
| `CMCODER_CONFIG_DIR` | another folder instead of `~/.cmcoder` |
| `CMCODER_GIT_BASH_PATH` | Windows: `bash.exe` when Git for Windows is somewhere unusual |
| `CMCODER_SANDBOX=off` | turns the Bash sandbox off ([sandbox.md](sandbox.md)) |
| `CMCODER_EMBEDDING_MODEL`, `CMCODER_RAG=on\|off`, `CMCODER_CHROMA_API_KEY` | code search ([code-search.md](code-search.md)) |
| `CMCODER_TELEMETRY=1` | usage metrics ([telemetry.md](telemetry.md)) |
| `CMCODER_SYMBOLS=basic\|unicode` | the terminal's symbol set ([sessions.md](sessions.md#symbols-on-windows)) |

`OPENAI_BASE_URL` is used if `CMCODER_BASE_URL` isn't set; `OPENAI_API_KEY`
only together with `OPENAI_BASE_URL`, so an unrelated OpenAI key is never sent
to your gateway. `CMCODER_API_KEY` overrides the key stored by `cmcoder login`.

## Settings you'll meet

| Setting | Default | Guide |
|---|---|---|
| `providers`, `model`, `smallFastModel` | — | [gateway.md](gateway.md) |
| `visionModel` | — | [images.md](images.md) |
| `modelProfiles` | from the model's name | [gateway.md](gateway.md#model-profiles) |
| `permissions` (`allow`, `deny`, `defaultMode`, `highRiskCommands`) | ask before edits and commands | [permissions.md](permissions.md) |
| `sandbox` | on (Linux, WSL2, macOS) | [sandbox.md](sandbox.md) |
| `mcpServers` | — | [mcp.md](mcp.md) |
| `hooks` | — | [hooks.md](hooks.md) |
| `rag` | off until set up | [code-search.md](code-search.md) |
| `maxTurns` | 50 model calls per request | [team.md](team.md#step-9-trust-the-project-and-give-the-lead-room) |
| `subagentModel`, `subagentMaxTurns`, `maxParallelSubagents` | `smallFastModel` for `explore`; 100; 4 | [agents.md](agents.md) |
| `critic` | off | [critic.md](critic.md) |
| `autoCompact`, `autoCompactThreshold` | on, 0.8 | [sessions.md](sessions.md#long-conversations-the-context-window) |
| `persistSessions`, `cleanupPeriodDays` | on, 30 days | [sessions.md](sessions.md) |
| `telemetry` | off | [telemetry.md](telemetry.md) |
| `ui` | classic terminal (`"textual"`: the full-screen UI) | [../guides/terminal.md](../guides/terminal.md) |
| `symbols` | chosen by the terminal | [sessions.md](sessions.md#symbols-on-windows) |

## Your company's settings for everyone

An administrator can ship a `settings.json` with the build
(`branding/settings.template.json`): it becomes each developer's
`~/.cmcoder/settings.json` the first time cmcoder starts, so they only add
their API key. See [../admin/README.md](../admin/README.md#the-settings-template).

## Checking

```bash
cmcoder doctor     # every layer, what's in effect, what's ignored and why
```
