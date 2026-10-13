# Skills: know-how loaded when it's needed

A skill is a folder with a `SKILL.md`: instructions for one kind of task,
plus any templates, examples or scripts it needs. Only each skill's name and
description are in the agent's prompt; when a task matches, the agent loads
the rest. Many skills cost almost nothing until they're used. The format is
Claude Code's.

## Where they go

| Folder | For |
|---|---|
| `~/.cmcoder/skills/<name>/SKILL.md` | you, in every project |
| `.cmcoder/skills/<name>/SKILL.md` | everyone on this repository (commit it) |

Yours win on a name clash.

## An example: release notes in your format

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

Put `template.md` next to it. Ask "write the release notes": the agent loads
the skill with the `Skill` tool, and `template.md` with
`Skill(skill="release-notes", file="template.md")`.

| Header | Meaning |
|---|---|
| `name` | the skill's name (default: the folder's name) |
| `description` | **when** to use it: the agent decides from this alone, so be specific |

## Good skills for a team

| Skill | Holds |
|---|---|
| `coding-standards` | naming, structure, error handling, logging rules |
| `testing-guide` | the test framework, where tests go, how to run them |
| `api-design` | your REST conventions, error format, versioning |
| `release-checklist` | the steps and the template for a release |
| `db-migrations` | how migrations are written, named and checked |

Fill them with your real rules: that's what makes the result match your
standards. The team setup has two complete examples ([team.md](team.md#step-3-the-know-how-cmcoderskills)).

## Skill, memory or agent?

- **`CMCODER.md`** is read every session: short rules for everything
  ([memory.md](memory.md)).
- **A skill** is read only when it's needed: longer know-how for one kind of task.
- **A subagent** is a separate worker with its own conversation and tools
  ([agents.md](agents.md)). Roles (developer, tester) are subagents; they load
  the skills they need: give them the `Skill` tool and tell them which to load.

## Trust

A skill is only text: it runs nothing by itself and grants no permissions.
Scripts it mentions go through the Bash tool's permission checks like any
other command ([permissions.md](permissions.md)). So a repository's skills
are used in untrusted projects too.

`cmcoder doctor` lists the skills it found ("Commands, agents and skills").
