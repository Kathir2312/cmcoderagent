# Subagents: helpers and team roles

The agent can hand a self-contained job to a **subagent** with the `Task`
tool: a fresh conversation with its own instructions, tools and possibly a
smaller model, which returns one report. Searches and side quests then don't
fill the main conversation, and several can run at the same time. Roles in a
team (PM, developer, tester) are subagents too ([team.md](team.md)).

## Built in

| Agent | Tools | Model |
|---|---|---|
| `general-purpose` | every tool | the main model |
| `explore` | read-only: Read, Glob, Grep | `subagentModel`, else `smallFastModel`, else the main model |

Ask for them in plain words: "use explore subagents to find where orders are
validated, one per project, then summarise".

## Your own

| File | For |
|---|---|
| `~/.cmcoder/agents/<name>.md` | you, in every project |
| `.cmcoder/agents/<name>.md` | everyone on this repository (used in a trusted project) |

`.cmcoder/agents/reviewer.md`:

```markdown
---
name: reviewer
description: Reviews the current changes for bugs. Use after finishing a change.
tools: Read, Grep, Glob, Bash
model: small
---
You review code changes. Run `git diff` to see them, read the files around
them, and report real bugs only, each with file:line and a suggested fix.
```

| Header | Meaning |
|---|---|
| `name` | what the agent calls it (letters, digits, `-`, `_`) |
| `description` | **when** to use it: the main agent chooses from this, so say it clearly |
| `tools` | optional: only these tools (default: all). `Skill` lets it load skills; `CodeSearch` works when the project is indexed; `mcp__<server>` gives all of an MCP server's tools |
| `model` | optional: `inherit` (the main model, the default), `small` (your `smallFastModel`), or a model name |

The body is its instructions. It also gets the project's memory files, the
skills list and the environment, but **never your conversation**: only the
task the main agent writes for it.

## What a subagent may do

- It asks permission like the main agent (same rules and mode); its questions
  come to you one at a time.
- It runs `PreToolUse`/`PostToolUse` hooks, and `SubagentStop` when it
  finishes ([hooks.md](hooks.md)).
- It can't start subagents itself (one level), and its file changes can be
  undone with `/rewind` ([sessions.md](sessions.md)).
- It may make `subagentMaxTurns` model calls (default 100, separate from
  `maxTurns`). It's told when 5 are left; at the limit it makes one last call
  without tools to write its report, so the main agent always gets what it
  found (marked as possibly incomplete).

## Several at once

Several Task calls in one reply **run at the same time**, up to
`maxParallelSubagents` (default 4, at most 16; 1 runs them one after
another). This needs a model that sends several tool calls in one reply: on by
default for Qwen3, otherwise `"parallelToolCalls": true` in `modelProfiles`
([gateway.md](gateway.md#model-profiles)). Keep parallel agents on separate
files: two changing the same file overwrite each other.

## Watching and stopping them

**The agent map** shows the turn's subagents while they work: state (queued,
running, waiting for permission, stopping, done, stopped, failed), steps used
of the limit, tool calls, tokens, time, and what each is doing now.

```
├─ ◐ 1. Analyse TW.Core  explore · 14/100 steps · 22 tools · 31k tokens · 1m12s
│     └ Read(Controllers/OrderController.cs)
├─ ⏸ 2. Analyse TW.Api  waiting for permission · general-purpose · 6/100 steps · …
└─ ○ 3. Analyse TW.Web  explore · queued
```

| Where | Map | Stop one |
|---|---|---|
| Terminal | under the spinner; `/agents map` shows the last turn's again; `-v` tags each step line with its task | Ctrl+C, then its number (`a` stops everything) |
| TUI (`--tui`) | above the input; Ctrl+G (or `/agents map`) opens the **agent navigator**: arrow keys through the tree, the selected agent's steps and report on the right | S in the navigator, or `/agents stop <n>` |
| VS Code, other IDEs | above the input, and the **Agent Navigator** tab (the hierarchy icon in the chat's title bar): a live mind map; click an agent for its steps and report | its **Stop** button |

Stopping one subagent leaves the turn and the others running: it writes its
report from what it has so far. `/agents` lists the agent types and this
session's runs; `/agents <n>` shows a run's steps and report.

## The critic is an agent too

The critic, which checks each answer before you see it, is a read-only agent;
a `critic.md` in your agents folder replaces its instructions
([critic.md](critic.md)).

## Trust

A repository's agents are used only in a trusted project
([permissions.md](permissions.md#trusting-a-project)); yours always are.
`cmcoder doctor` lists them, and the ones ignored and why.
