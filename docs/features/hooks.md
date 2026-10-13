# Hooks: your own checks on agent events

A hook runs one of your commands when something happens: a formatter after
every edit, a check that blocks a risky command, extra context with every
prompt. The format is Claude Code's.

## An example: format and lint after every edit

`.cmcoder/settings.json` (the team's, committed) or `~/.cmcoder/settings.json`
(yours):

```json
{
  "hooks": {
    "PostToolUse": [
      {"matcher": "Edit|Write", "hooks": [{"type": "command", "command": "ruff format --quiet . && ruff check --quiet . >&2 || exit 2"}]}
    ],
    "PreToolUse": [
      {"matcher": "Bash", "hooks": [{"type": "command", "command": "./scripts/check-command.sh", "timeout": 30}]}
    ]
  }
}
```

The first hook formats the project after each edit; when the linter finds
something, exit 2 sends its message ("line 3: unused import") back to the
model, which fixes it. The second runs your script before every Bash command.

## Events

| Event | When | Exit 2 (block) means |
|---|---|---|
| `PreToolUse` | before a tool runs (`matcher`: the tool) | the tool doesn't run; stderr tells the model why |
| `PostToolUse` | after a tool ran (`matcher`: the tool) | the model is told (stderr) |
| `UserPromptSubmit` | you send a message | the prompt isn't sent |
| `SessionStart` | a session starts | — (plain stdout is added as context) |
| `Stop` | the agent is about to finish its answer | the model keeps working, with the reason |
| `SubagentStop` | a subagent finishes | as `Stop`, for the subagent |
| `PreCompact` | before a long conversation is summarised | — |
| `Notification` | cmcoder needs your attention (a permission question) | — |

`matcher` is a tool name or a regular expression over it (`Edit|Write`,
`mcp__jira__.*`); leave it out (or `*`) for every tool.

## How a hook runs

- It gets the event as **JSON on stdin** (the tool's name and input, the
  prompt, ...).
- It runs **in the project folder** with the Bash tool's shell (Git Bash on
  Windows), so one command works on every OS. `CMCODER_PROJECT_DIR` is set.
- `timeout` is in seconds (default 60).
- **Exit 0:** fine. Plain stdout from `UserPromptSubmit` and `SessionStart` is
  added to the model's context.
- **Exit 2:** block (see the table); stderr is the reason the model gets.
- **Any other exit:** a warning for you; nothing is blocked.

JSON on stdout can decide instead:

```json
{"hookSpecificOutput": {"permissionDecision": "allow", "permissionDecisionReason": "a known-safe build"}}
```

`permissionDecision` is `allow`, `ask` or `deny` (for `PreToolUse`). An
`allow` skips a normal permission prompt, but **never** a deny rule or a
high-risk command. `{"decision": "block", "reason": "..."}` blocks, like exit 2.

## Trust

- Your hooks (user settings) and the managed settings' hooks always run.
- A **repository's** hooks run only in a trusted project, and each command
  asks once before it first runs (and again if it changes)
  ([permissions.md](permissions.md#trusting-a-project)).
- Administrators can allow only their own hooks: `allowManagedHooksOnly` in
  the managed settings.

## Ideas for a team

| Hook | Does |
|---|---|
| `PostToolUse` on `Edit\|Write`: the formatter | every change in the team's style |
| `PostToolUse` on `Edit\|Write`: the type checker, exit 2 on errors | the model fixes its own type errors |
| `PreToolUse` on `Bash`: a script that rejects `git push` to `main` | a rule the model can't skip |
| `UserPromptSubmit`: print the current sprint's ticket | context with every message |
| `Stop`: run the tests, exit 2 on failure | the agent doesn't stop on red tests |

With the team roles ([team.md](team.md)), `SubagentStop` runs when each role
reports; subagents also run `PreToolUse`/`PostToolUse` hooks like the main
agent ([agents.md](agents.md)).

## Checking

`cmcoder doctor` lists the hooks in effect and where each comes from. A hook
that keeps failing shows its stderr as a warning in the chat.
