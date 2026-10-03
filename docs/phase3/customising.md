# Customising cmcoder

Everything here is files: put them in your home folder for yourself, or in
the repository for your team. The formats are Claude Code's, so existing
examples work.

| You want | Add | Yours (every project) | The team's (in the repository) |
|---|---|---|---|
| New tools (GitHub, Jira, a database) | an MCP server | `mcpServers` in `~/.cmcoder/settings.json`, or `cmcoder mcp add` | `.mcp.json` |
| Automatic checks (format after an edit, block a command) | a hook | `hooks` in `~/.cmcoder/settings.json` | `hooks` in `.cmcoder/settings.json` |
| A shortcut for a prompt you type often | a slash command | `~/.cmcoder/commands/<name>.md` | `.cmcoder/commands/<name>.md` |
| A helper for side jobs (search, review) | a subagent | `~/.cmcoder/agents/<name>.md` | `.cmcoder/agents/<name>.md` |
| Know-how loaded only when needed | a skill | `~/.cmcoder/skills/<name>/SKILL.md` | `.cmcoder/skills/<name>/SKILL.md` |

## What a repository needs your trust for

A repository you clone could contain any of these files, so the ones that
**run something or change permissions** are used only after you trust the
project, and each server or hook is approved once more before it first runs:

| From a repository | Untrusted | Trusted (`cmcoder trust`, or a workspace VS Code trusts) |
|---|---|---|
| MCP servers (`.mcp.json`) | ignored | each one asks once before it starts |
| hooks | ignored | each command asks once before it first runs |
| subagents | ignored | used |
| slash commands | used (they're prompts you run) | used, and their `allowed-tools` apply |
| skills, `CMCODER.md` | used (text only) | used |

Yours always win over a repository's on a name clash, and a repository's
files must really be inside it (a symlink to `~/.ssh` is ignored).
`cmcoder trust` shows what trusting a project would enable;
`cmcoder doctor` lists everything that's active and where it comes from.

## Examples

### An MCP server: your tickets

```bash
cmcoder mcp add jira --url https://mcp.example.com/jira --header "Authorization: Bearer \${JIRA_TOKEN}"
cmcoder mcp list --check
```

`${JIRA_TOKEN}` is read from your environment when the server starts, so the
token never sits in a file. The model now has `mcp__jira__*` tools; they ask
before running unless you allow them (`"allow": ["mcp__jira"]` for all of
them). Type `/mcp` to see the servers' state. For one run only:
`cmcoder --mcp-config servers.json`.

### A hook: format Python after every edit

`.cmcoder/settings.json`:

```json
{
  "hooks": {
    "PostToolUse": [
      {"matcher": "Edit|Write", "hooks": [{"type": "command", "command": "ruff format --quiet . && ruff check --quiet . >&2 || exit 2"}]}
    ]
  }
}
```

The hook gets the tool call as JSON on stdin. Exit 2 sends its stderr back to
the model ("line 3: unused import"), so the model fixes it. Hooks run with
bash (Git Bash on Windows).

### A slash command: a review checklist

`.cmcoder/commands/review.md`:

```markdown
---
description: Review a file against our checklist
argument-hint: <file>
allowed-tools: Read, Grep, Bash(git diff:*)
---
Review $1 against our checklist:
1. Errors are handled, not ignored.
2. New functions have tests.
3. No secrets or URLs are hard-coded.
Report each problem with file:line. Don't change any file.
```

Run it with `/review src/app.py` (Tab completes the name in the terminal;
VS Code shows a list as you type `/`).

### A subagent: a reviewer with fewer tools

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

The main model hands it a job with the `Task` tool ("use the reviewer to
check my change"); its steps show inside the Task call, and only its report
comes back to the main conversation. `model: small` uses your
`smallFastModel`. The built-in `explore` agent does read-only searches.

### A skill: release notes in your format

`.cmcoder/skills/release-notes/SKILL.md`:

```markdown
---
name: release-notes
description: Writes release notes in our format. Use when asked for release notes or a changelog entry.
---
1. Find the last tag: `git describe --tags --abbrev=0`.
2. List the changes since then: `git log --oneline <tag>..HEAD`.
3. Fill in template.md (in this skill's folder), grouping changes by area.
```

Put `template.md` next to it. Only the description is in the system prompt;
the model loads the rest with the `Skill` tool when you ask for release
notes, and `template.md` with `Skill(skill="release-notes", file="template.md")`.

## Checking it all

```bash
cmcoder doctor            # what's configured, where it's from, what's ignored and why
cmcoder trust             # what trusting this project would enable
cmcoder mcp list --check  # starts each MCP server and lists its tools
```
