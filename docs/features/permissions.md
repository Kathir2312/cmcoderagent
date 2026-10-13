# Permissions, modes and trust

cmcoder asks before it changes files or runs commands, unless a mode or a
rule says otherwise. This page is how to ask less without giving away more.

## Modes

| Mode | Behaviour |
|---|---|
| `default` | reads inside the project are allowed; edits and commands ask first |
| `acceptEdits` | file edits inside the project are allowed; commands still ask |
| `auto` | asks only for risky actions: high-risk commands, pushes, publishing, deploys, uploads, package installs, protected files, files outside the project or secret |
| `plan` | read-only; edits and non-read-only commands are refused |
| `bypassPermissions` | everything allowed except deny rules and high-risk commands (use with care) |

Change it with **Shift+Tab** (terminal), `/mode`, the mode menu in the IDE
chats, `--permission-mode`, or `"permissions": {"defaultMode": "..."}`.

**Read-only commands never ask**, in any mode: `grep`, `find`, `cat`, `head`,
`tail`, `wc`, `ls`, `tree`, `sort`, `rg`, `sed -n 'Np'`, read-only `git`
(`status`, `diff`, `log`, `show`, `blame`, `grep`, `ls-files`, ...), in pipes
and chains too (`grep -rn Foo src | head`, `cd src && ls`), as long as they
name no file outside the project or secret file, write nothing (`> /dev/null`
and `2>&1` are fine) and use no writing option (`find -delete`, `sort -o`).

## Rules

```json
"permissions": {
  "allow": ["Edit(src/**)", "Bash(npm test:*)", "Bash(git status)", "mcp__jira"],
  "deny":  ["Bash(git push:*)", "Read(secrets/**)"]
}
```

| Rule | Covers |
|---|---|
| `Read`, `Edit`, `Write`, `Bash`, `Grep`, ... | the whole tool |
| `Edit(src/**)` | edits (and writes) to matching paths |
| `Bash(npm test:*)` | commands starting with `npm test` |
| `Bash(git status)` | exactly that command |
| `mcp__jira`, `mcp__jira__create_issue` | an MCP server's tools, or one of them |

- **Deny rules win** over allow rules, in every layer and mode.
- A command line is checked part by part (`a | b && c`): it runs when every
  part is read-only or allowed by a rule, and a file it writes (`> out.log`)
  is allowed like an edit. A deny rule for any part denies the whole line,
  also inside `$( )`. A part that runs a hidden command (`$( )`, backticks) is
  never covered by a prefix rule.
- **Always allow** saves one rule per part that needs it, in
  `.cmcoder/settings.local.json`: answering it for
  `dotnet build 2>&1 | tail -30` saves `Bash(dotnet build:*)`, so later builds
  with other options or pipes run.
- Files such as `.env`, `*.pem`, `*.key` and `secrets/` are never read (or
  shown in Grep results) unless an allow rule names them.
- Editing `.cmcoder/`, `.git/` or `~/.cmcoder/` always asks, except in
  `bypassPermissions`.

Rules from every settings layer are combined ([settings.md](settings.md)).
For one run: `--allowedTools "Bash(pytest:*)"`.

## High-risk commands

`rm -rf`, `git clean`, `git reset --hard`, force push, `sudo`, `curl … | sh`,
`del /s`, `Remove-Item -Recurse`, ... **always ask**, in every mode including
`bypassPermissions`, and are never remembered or approved by allow rules (or
hooks); `-p` runs refuse them. To block them entirely:
`"permissions": {"highRiskCommands": "deny"}`.

## File work goes through the file tools

Models like Qwen sometimes write files with `cat > f << EOF` or read them with
`cat`/`grep` in Bash, which would need your approval each time and skips
cmcoder's checks. Such plain file work isn't run: the model is told which tool
to use instead (Write, Read, Edit, Grep, Glob), with no prompt for you.
Pipelines and other real shell work run as usual, and the model can send the
same command again if it really needs the shell. Turn it off per model with
`"modelProfiles": [{"match": "qwen3*", "steerBashFileWork": false}]`.

## Trusting a project

A repository you clone could bring settings, hooks, MCP servers and agents.
The ones that run something or loosen permissions are used only once you
trust the project ([README.md](README.md#what-a-repository-needs-your-trust-for)):

| Where | How |
|---|---|
| Terminal | `cmcoder trust` in the project folder (it lists what it would enable; `cmcoder trust --revoke` undoes it), or `--trust-project` for one run |
| VS Code | trust the workspace (VS Code's own workspace trust) |
| Visual Studio | cmcoder's options: "Use the solution's own .cmcoder settings" |
| Eclipse, NetBeans | cmcoder's options: "Use the project's own .cmcoder settings" |
| IntelliJ IDEA and the other JetBrains IDEs | trust the project when the IDE asks (the IDE's own project trust) |

Even in a trusted project, each MCP server and hook asks once before it first
runs, and a repository never sets the gateway.

## For the team

- **Shared rules:** put allow and deny rules in `.cmcoder/settings.json` and
  commit them; everyone gets the same baseline (allow rules apply once the
  project is trusted).
- **Rules no one can loosen:** managed settings, installed by IT, can turn off
  `bypassPermissions` or `auto`, add deny rules, allow only their own allow
  rules, and deny high-risk commands
  ([../admin/README.md](../admin/README.md#5-managed-settings-rules-developers-cant-change)).
- **Safer commands:** the sandbox lets commands run without asking because
  they can't reach what they shouldn't ([sandbox.md](sandbox.md)).
- **Checks the model can't skip:** a `PreToolUse` hook ([hooks.md](hooks.md)).
