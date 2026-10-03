# Phase 3 security review: SAST and SCA

**Date:** 3 October 2026. **Scope:** the whole repository as of Phase 3 item
7, with the closest look at what Phase 3 added: the MCP client
(`mcp_client.py`, `cli/mcp_cmd.py`, `--mcp-config`), hooks (`core/hooks.py`),
custom slash commands (`core/commands.py`), subagents (`core/subagents.py`),
skills (`core/skills.py`), their front-end parts (REPL, TUI, the stdio
protocol, the VS Code panel) and the new dependencies (the `mcp` SDK and the
packages it brings).

**Result:** 2 issues fixed (1 medium, 1 low). One of them, the medium one,
was older than Phase 3 (memory files) and was found while checking the new
loaders, which had the same flaw. No known-vulnerable dependency. Everything
the scanners still report was traced to the code and is a false positive or
accepted with a reason (below).

## How it was tested

| Kind | Tool | Version | What it covered |
|---|---|---|---|
| SAST (Python) | Bandit | 1.9.4 | `src/`, `evals/run.py` (all rules) |
| SAST (Python, TypeScript, JavaScript, secrets) | Semgrep | 1.179.0, rules from `semgrep/semgrep-rules` commit `a84ff9c` (22 Sep 2026): `python`, `javascript`, `typescript`, `generic/secrets`, `ai`, `bash` | 898 rules on 152 files: `src`, `vscode/src`, `vscode/test`, `evals`, `tests` |
| SCA (Python) | pip-audit | 2.10.1 | all 67 packages pinned in `uv.lock` (runtime + dev), the `mcp` SDK's included |
| SCA (npm) | npm audit | npm 10.9.4 | all 202 extension packages (`package-lock.json`) |
| Manual | review + proof of concept | | every scanner finding traced to the code; the trust boundaries Phase 3 adds checked by hand (below) |

As in Phase 2, Semgrep's registry is blocked by this environment's network
policy, so the same public rules were cloned from GitHub and run locally.

## Findings and fixes

| # | Severity | Finding | Found by | Status |
|---|---|---|---|---|
| 1 | **Medium** | A repository's memory file, command, agent or skill could be a symlink to a file outside the project (or to `.env`), whose contents were then sent to the model | manual, proof of concept | Fixed |
| 2 | Low | Approving a project's MCP server showed only its command or URL, not the `env` and `headers` it sends (e.g. `Authorization: Bearer ${GITHUB_TOKEN}`) | manual | Fixed |

### 1. Repository files that point outside the repository

cmcoder reads some repository files by itself and gives their text to the
model: memory files (`CMCODER.md`, `CMCODER.local.md`, in every system
prompt, since Phase 1) and, since Phase 3, custom commands, agents and skills.
Git keeps symbolic links, so a cloned repository can contain
`CMCODER.md -> ~/.aws/credentials`.

Proof of concept (on the code before the fix):

```
CMCODER.md                 -> ~/.aws/credentials
.cmcoder/commands/review.md -> ~/.aws/credentials

before fix: memory ['AKIA-SECRET'] | /review body: AKIA-SECRET
after fix:  memory []              | commands: []
```

The first one needs no action from the user: opening cmcoder in the folder
sends the file in the system prompt. The secret-file protection the Read tool
has (`.env`, keys, `secrets/`) didn't apply either: a link to `.env` inside
the project was read the same way. The model gateway is the company's, so
this is a leak to the model rather than to the repository's author directly,
but a model that has seen a secret can be steered (by the same repository) to
repeat it in a file or a command.

**Fix:** `sensitive.safe_project_file`: a file cmcoder reads from a
repository on its own must resolve (links followed) to a path inside the
project and must not be a secrets file. Applied to memory files (except your
own `~/.cmcoder/CMCODER.md`), commands, agents, skills (the folder and its
`SKILL.md`). The `Skill` tool's other files were already confined to the
skill's folder after resolving links, and checked with `is_secret`.

Tests: `tests/test_project_files.py` (skipped where symlinks can't be
created, e.g. Windows without developer mode).

### 2. What an MCP server approval shows

A trusted project's `.mcp.json` server is approved once before it first
starts. The prompt (and `cmcoder mcp approve`) showed `describe()`: the
command line or the URL. A server's `env` and `headers` were not shown,
though they can carry your environment variables, expanded at start:
`"headers": {"Authorization": "Bearer ${GITHUB_TOKEN}"}` sends your token to
the server's URL. A stdio server gets only a minimal environment from the MCP
SDK, so `env` is also how a configuration hands it secrets.

**Fix:** `McpServerConfig.approval_details()`: the prompt shows `runs`, `cwd`,
`env` and `headers` as written (`${VAR}` not expanded, so nothing secret is
displayed), plus **"reads your environment variables: GITHUB_TOKEN, ..."**.
The CLI, the TUI and VS Code show it (VS Code's card lists every field). The
approval was already tied to the exact configuration (a fingerprint), so a
changed header asks again.

Test: `tests/test_mcp.py::test_approval_shows_env_headers_and_variables`.

## Trust boundaries checked by hand

| What a repository (or a server) controls | What it can do | Why that's acceptable / how it's limited |
|---|---|---|
| `.mcp.json`, project `mcpServers` | start a program, connect to a URL | Only in a trusted project, and each server approved once (tied to its exact configuration); off with `-p`; managed allow/deny lists |
| project `hooks` | run shell commands on events | Only in a trusted project, each command approved once; `allowManagedHooksOnly`; a hook's "allow" never overrides a deny rule or a high-risk command |
| `.cmcoder/commands/*.md` | the text of a prompt the user chooses to run | Used without trust (it's text the user runs on purpose); `allowed-tools` only in a trusted project, only for that turn, never with managed-only rules; can't replace built-in commands or the user's own; must be inside the project (finding 1) |
| `.cmcoder/agents/*.md` | a subagent's prompt and tool list | Only in a trusted project; the subagent uses the main permission policy, prompts and hooks; can't start subagents; can't replace built-in agents or the user's own |
| `.cmcoder/skills/*/SKILL.md` | text in the system prompt (description) and in a tool result | Used without trust, like `CMCODER.md`: text only, no permissions; files confined to the skill's folder and the project, secrets refused |
| an MCP server's tools, prompts, resources | tool results and prompt text | The server was configured by the user or approved; every tool call goes through the permission engine (asks by default) |
| `--mcp-config FILE` | servers for one run | The user names the file on the command line, like their own settings; managed lists still apply |
| subagent output | the Task result | Goes to the main model only; each of its tool calls was checked like the main agent's, shown with `parent_tool_use_id` |

Accepted risks (as in Claude Code): skill descriptions and memory files from
a repository are prompt text the model reads, so a malicious repository can
try to instruct the model; what the model then does still goes through the
permission engine. A project MCP server can read the environment variables
its approved configuration names.

## Scanner results, reviewed

| Tool | Result |
|---|---|
| Bandit | 0 high, 0 medium, 54 low (all reviewed: categories below) |
| Semgrep | 64 findings, reviewed below |
| pip-audit | No known vulnerabilities (67 packages) |
| npm audit | 0 vulnerabilities |

| Finding | Where | Why it's not a problem |
|---|---|---|
| Bandit B603 / B607 / B404, Semgrep `dangerous-subprocess-use-audit` | `compat.py`, `prompt.py`, `evals/run.py` | Argument lists, no shell; programs by full path (Phase 2 finding 3); `evals/run.py` runs `git` in its own temporary copies |
| Bandit B101 `assert` | several | Type narrowing for the checker on values set earlier; nothing security-relevant depends on them |
| Bandit B110 `try/except/pass` | `auth.py`, `transport.py`, `agent.py`, `compat.py` | Optional steps (keyring, OS trust store, a session title) whose failure must not stop cmcoder |
| Bandit B311 `random` | `openai_compat.py` | Retry jitter, not security |
| Semgrep `open-never-closed` | `mcp_client.py` (server log file) | Closed by `_closing` when the server's connection ends |
| Semgrep `hooks-unconditional-allow-generic` | `hooks.py` | Matches the docstring's example of Claude Code's hook JSON, not a configuration |
| Semgrep `is-function-without-parentheses`, `return-not-in-function`, `useless-inner-function` | `agent.py`, `cli/*`, `permissions.py`, `subagents.py`, `settings.py` | Attributes named `is_*` (`is_error`, `is_subagent`), a lambda default, inner async jobs: code-quality heuristics, not defects |
| Semgrep `dangerous-asyncio-create-exec-audit` | `tools/search.py` | ripgrep by full path, argument list (Phase 2) |
| Semgrep `html-in-template-string`, `prohibit-jquery-html`, `jquery-insecure-method`, `insufficient-postmessage-origin-validation` | `vscode/src` | As in Phase 2: fixed panel HTML with a nonce; DOM `append(element)`; messages only from VS Code's host. New Phase 3 text in the panel (command descriptions, subagent steps) is set with `textContent` (tested: `<b>` shows as text) |
| Semgrep `tempfile-without-flush`, `arbitrary-sleep` | `doctor.py`, `compat.py` | As in Phase 2 |
| Semgrep `detect-generic-ai-*`, `detect-anthropic` | | Informational: the code talks to model APIs |
| Semgrep `pass-body-*`, `python36-compatibility-*` | eval task repositories, `evals/run.py` | Sample code for the evals; Python 3.11+ only |

## What runs in CI

The `security` job (Bandit at medium or higher, pip-audit, npm audit at high
or higher) covers the new code and packages with no change. Semgrep stays a
manual step while its registry is unreachable from here.
