# Project memory: CMCODER.md

`CMCODER.md` is what the agent reads at the start of every session and
follows as instructions: how to build and test the project, its layout, the
team's rules. It's the place for anything you'd otherwise repeat in every
conversation.

## Where it's read from

| File | For |
|---|---|
| `~/.cmcoder/CMCODER.md` | you, in every project |
| `CMCODER.md` (or `AGENTS.md`) at the project root | everyone on the repository (commit it) |
| `CMCODER.md` in subfolders, down to the folder you work in | rules for one part of the project |
| `CMCODER.local.md` | you, this project only (keep it out of git) |

All of them are used together (about 40,000 characters in all). `AGENTS.md`
is read too, so a repository that already has one for other tools works as
it is.

## What to put in it

```markdown
# Project notes
- .NET 8 solution: src/ (code), tests/ (xUnit). Build: `dotnet build`; test: `dotnet test`.
- Run the tests after every change; fix what you broke.
- Don't change files in src/Generated/ (generated from the OpenAPI spec).
- Logging: ILogger<T>, never Console.WriteLine.
- Never commit or push; I review first.
```

Keep it short and concrete: commands, folders, rules. Long how-tos belong in
a **skill**, which is loaded only when it's needed ([skills.md](skills.md)).

This repository's own `CMCODER.md` is an example.

## Memory, skills, commands: which one?

| You want | Use |
|---|---|
| Rules for every conversation ("never push", "build with ...") | `CMCODER.md` |
| Know-how for one kind of task ("how we write release notes") | a skill |
| A prompt you start yourself (`/review file`) | a slash command ([commands.md](commands.md)) |
| A process the agent runs end to end (a team) | `CMCODER.md` for the process, subagents for the roles ([team.md](team.md)) |

## Trust

`CMCODER.md` is text: it's used in every project, trusted or not, but it can
only ask. It grants no permissions and runs nothing: the agent's tools still go
through your permission rules ([permissions.md](permissions.md)).
