# Slash commands: prompts you use often

A Markdown file is a command. `/review src/app.py` then sends your team's
review checklist with the file name filled in. The format is Claude Code's.

## Where they go

| File | Command | For |
|---|---|---|
| `~/.cmcoder/commands/review.md` | `/review` | you, in every project |
| `.cmcoder/commands/review.md` | `/review` | everyone on this repository (commit it) |
| `.cmcoder/commands/db/migrate.md` | `/db:migrate` | subfolders are namespaces |

Yours win on a name clash.

## An example: a review checklist

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

Run it with `/review src/app.py`.

| Header | Meaning |
|---|---|
| `description` | shown in `/help` and the completion list |
| `argument-hint` | shown while you type the arguments |
| `allowed-tools` | allow rules for that one turn ([permissions.md](permissions.md)); a repository's commands get them only in a trusted project, and never against managed-only rules |

In the body, `$ARGUMENTS` is everything after the command and `$1`..`$9` its
words (quotes group words). With no placeholder the arguments are added at
the end.

A command only becomes a prompt: it never runs anything by itself.

## Using them

- `/help` lists them; they complete as you type in the terminal, the TUI and
  every IDE's chat.
- MCP servers' prompts are commands too: `/mcp__<server>__<prompt> args`
  ([mcp.md](mcp.md)).

## Built-in commands

`/help`, `/clear`, `/resume`, `/rewind`, `/todos`, `/compact`, `/model`,
`/mode`, `/cost`, `/mcp`, `/index`, `/agents`, `/critic`, `/image`, `/exit`
([../guides/terminal.md](../guides/terminal.md#slash-commands-type-them-in-the-chat)).
Your own can't replace them.

## With the other features

A command is the easy way to start a whole process: `/feature <request>` that
tells the lead to follow the team process in `CMCODER.md`
([team.md](team.md#step-5-one-command-for-the-whole-flow-cmcodercommandsfeaturemd)).
A command can also tell the model to use a skill ("Load the release-notes
skill, then ...") or a subagent ("use the reviewer agent").
