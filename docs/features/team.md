# A project team in cmcoder: PM, developer, tester, DevOps

cmcoder can work like a small team: the main agent is the **team lead**, and
each role (product manager, developer, tester, DevOps engineer) is a
**subagent** with its own instructions and tools. The lead plans the work,
hands each piece to the right role, reads their reports, and keeps going
until the feature is done. This page sets that up in about 15 minutes.

## How it works

**Roles are subagents, know-how is skills.**

| | Skill (`.cmcoder/skills/<name>/SKILL.md`) | Subagent (`.cmcoder/agents/<name>.md`) |
|---|---|---|
| What it is | Instructions: know-how | A separate worker: its own prompt, conversation, tools and model |
| Who uses it | Any agent that loads it (the `Skill` tool) | The main agent hands it a job (the `Task` tool) |
| Good for | "Our coding standards", "how we test", "release checklist" | **Roles**: developer, tester, PM, DevOps |

A skill on its own doesn't create a team member: it's text the agent reads.
Roles are subagents, and each role loads the skills it needs.

**One feature, start to finish:**

```
You: "/feature Add password reset by email"
 │
 ▼
Main agent (team lead): reads the team process in CMCODER.md, plans with TodoWrite
 1. Task(pm, "stories + acceptance criteria → docs/stories/reset.md")
 │     └─ pm: its own conversation and tools → report
 2. Task(developer, "implement docs/stories/reset.md …")
 │     └─ developer: reads, edits, builds → report (files changed)
 3. Task(tester, "test these changes against the criteria …")
 4. FAIL? → Task(developer, "fix: <exact failures>") → tester again
 5. Task(devops, "add the new tests to CI")   (in parallel with other independent steps)
 6. Summary for you
```

- The lead picks a role by its **description**. It's the model's decision,
  so you can also say it: "use the tester agent".
- A role sees **only the task the lead writes** for it, plus its own
  instructions, the project's memory files, the skills list and the
  environment. It never sees your conversation with the lead.
- A role sends back **one report** (up to 30,000 characters): all the lead
  learns from it.
- Each role may make up to 100 model calls (`subagentMaxTurns`), then it must
  report.
- Several hand-offs in one reply run **at the same time**: 4 by default
  (`maxParallelSubagents`, up to 16).
- Every tool call a role makes goes through **your permission rules**; its
  questions come to you, one at a time.

## Step 1: The folders

The files go in your project (shared with the whole team through git):

```
<your project>\
  CMCODER.md                          the team process (the lead reads it every session)
  .cmcoder\
    agents\     pm.md  developer.md  tester.md  devops.md     the roles
    skills\     coding-standards\SKILL.md  testing-guide\SKILL.md   the know-how
    commands\   feature.md            /feature runs the whole flow
```

In a Command Prompt, in the project folder:

```bat
mkdir .cmcoder\agents .cmcoder\skills\coding-standards .cmcoder\skills\testing-guide .cmcoder\commands
```

(macOS, Linux: `mkdir -p .cmcoder/agents .cmcoder/skills/coding-standards .cmcoder/skills/testing-guide .cmcoder/commands`.)

**Just for you instead?** Put `agents\`, `skills\` and `commands\` in
`%USERPROFILE%\.cmcoder\` (`~/.cmcoder/`). They then work in every project,
with no trust step (step 6). A project's roles are used only in a trusted
project.

Rules for every file below:
- Plain text (Notepad is fine).
- The part between the two `---` lines is the header. **Keep each header
  entry on one line.**
- The `description` is how the lead chooses a role: say clearly **when** to
  use it.

## Step 2: The roles (`.cmcoder\agents\`)

**`pm.md`**

```markdown
---
name: pm
description: Turns a feature request into user stories with acceptance criteria, written to docs/stories/. Use first for any new feature.
tools: Read, Grep, Glob, Write, Skill
---
You are the team's product manager. Read the relevant code to understand what exists.
Write docs/stories/<feature>.md with: goal, user stories, acceptance criteria (testable),
out of scope, open questions. Do not change code.
Report: the file you wrote and a 5-line summary of the stories.
```

**`developer.md`**

```markdown
---
name: developer
description: Implements a feature or fix from a written story or failure report, following the project's conventions. Use for every code change.
tools: Read, Grep, Glob, Edit, Write, Bash, Skill, CodeSearch
---
You are the team's developer. Load the coding-standards skill first.
Implement exactly what the task describes; keep changes minimal and in the existing style.
Build the project after your change and fix build errors.
Report: every file you changed (path + what changed), how you built it, anything left open.
```

**`tester.md`**

```markdown
---
name: tester
description: Writes and runs tests for a change against its acceptance criteria and reports each failure with details. Use after every code change.
tools: Read, Grep, Glob, Edit, Write, Bash, Skill, CodeSearch
---
You are the team's tester. Load the testing-guide skill first.
Write tests for each acceptance criterion you are given, then run the whole test suite.
Never change product code: only test files.
Report: PASS or FAIL; for each failure the test name, the exact error output and the
file and line it points to.
```

**`devops.md`**

```markdown
---
name: devops
description: Updates build and CI/CD files (pipelines, Dockerfiles, build scripts) for a change. Use when a feature needs build, CI or deployment changes.
tools: Read, Grep, Glob, Edit, Write, Bash, Skill
---
You are the team's DevOps engineer. Change only build, CI and deployment files.
Never push, deploy or publish: prepare the files and say what a person must run.
Report: files changed and how to verify them.
```

Header entries:

| Entry | Meaning |
|---|---|
| `name` | What the lead calls it (letters, digits, `-`, `_`) |
| `description` | When to use it: the lead reads this to choose |
| `tools` | Optional: only these tools (leave it out for all of them). `Skill` lets the role load skills; `CodeSearch` works when the project is indexed ([code-search.md](code-search.md)); `mcp__<server>` gives all of an MCP server's tools |
| `model` | Optional: `inherit` (the main model, the default), `small` (your `smallFastModel`), or a model name. With Qwen, keep the main model for developer and tester |

## Step 3: The know-how (`.cmcoder\skills\`)

**`coding-standards\SKILL.md`**

```markdown
---
name: coding-standards
description: Our coding conventions: naming, structure, error handling, logging. Use before writing or changing code.
---
- Follow the existing style of the file you change.
- Names: ... (your rules)
- Errors: ... (your rules)
- No new dependencies without saying so in the report.
```

**`testing-guide\SKILL.md`**

```markdown
---
name: testing-guide
description: How we write and run tests in this project. Use before writing or running tests.
---
- Test framework: ... (e.g. xUnit / JUnit / pytest)
- Where tests go: ...
- Run all tests: <your command, e.g. dotnet test>
- One test per acceptance criterion; name tests after the behaviour.
```

Fill in the `...` with your real rules: that's what makes the result match
your team's standards. A skill folder can hold more files (templates,
examples); the agent reads them when the skill points to them.

## Step 4: The team process (`CMCODER.md`, in the project root)

The lead reads `CMCODER.md` at the start of every session, so the process is
followed every time:

```markdown
# Team process
You are the team lead. For a feature request, run this process with the Task tool:
1. pm: stories and acceptance criteria → docs/stories/.
2. developer: implement the stories (give it the story file path and the criteria).
3. tester: test against the acceptance criteria (give it the developer's list of changed files).
4. If the tester reports FAIL: developer fixes (give it the exact failures), then tester again.
   At most 3 rounds, then stop and tell me what still fails.
5. devops: only if build or CI files need changes.
6. Finish with a summary: stories, files changed, test result, anything open.
Each agent sees only what you put in its task: always include file paths, criteria and
earlier reports it needs. Run independent steps in parallel.
```

If the project already has a `CMCODER.md`, add this section to it.

## Step 5: One command for the whole flow (`.cmcoder\commands\feature.md`)

```markdown
---
description: Run the whole team on a feature
argument-hint: <what to build>
---
Feature request: $ARGUMENTS

Follow the team process in CMCODER.md from step 1. Plan the steps with TodoWrite first.
```

`$ARGUMENTS` is whatever you type after `/feature`.

## Step 6: Trust the project, and give the lead room

1. **Trust the project**, so its roles are used:

   | Where | How |
   |---|---|
   | Terminal | `cmcoder trust` in the project folder |
   | VS Code | trust the workspace (VS Code's own workspace trust) |
   | Visual Studio | cmcoder's options: "Use the solution's own .cmcoder settings" |
   | Eclipse, NetBeans | cmcoder's options: "Use the project's own .cmcoder settings" |

2. **More steps for the lead.** A whole feature takes many hand-offs; by
   default the lead may make 50 model calls per request. In
   `%USERPROFILE%\.cmcoder\settings.json` add:

   ```json
   "maxTurns": 100
   ```

## Step 7: Check that it's all there

```bat
cmcoder doctor
```

The section **"Commands, agents and skills"** lists `pm`, `developer`,
`tester`, `devops`, both skills and `/feature`. Agents listed as ignored mean
the project isn't trusted yet (step 6).

In the chat or the terminal, `/agents` shows the agent types with their tools
and model.

## Step 8: Run it

In the IDE chat, or in the terminal (`cmcoder`):

```
/feature Add password reset by email: link valid for 30 minutes, one use only
```

What you'll see:
1. The lead writes a todo list: pm → developer → tester → (fixes) → devops → summary.
2. Each hand-off is a **Task** card with the role's name; inside it, that
   role's own steps (files read, edits, test runs).
3. Permission questions, one at a time as usual. `acceptEdits` or `auto` mode
   ask less; pushing, deploying and publishing always ask.
4. Independent steps (for example tester and devops) run at the same time.
5. The summary: the stories file, the changed files, the test result.

While it runs:

| Command | Does |
|---|---|
| `/agents` | Every role's run so far: status and steps |
| `/agents 2` | The full report of run 2 |
| `/agents stop 2` | Stops that one role |
| **Stop** | Stops everything; each role still writes its report |

## Limits to know

- **One level.** Roles can't start other roles or keep a todo list; the lead
  is the only coordinator.
- **Roles don't talk to each other.** The tester can't ask the developer
  anything. They share only the files on disk and what the lead passes on in
  tasks and reports.
- **Parallel roles can collide.** Two roles changing the same file at the
  same time overwrite each other. Keep parallel roles on separate files; the
  process above runs developer and tester one after the other for that
  reason.
- **Feature-sized work.** With a 32K context window and a 27B model, one
  feature per run goes well; "build the whole application" gets summarised
  too often (compaction) and the quality drops.
- **Outside systems.** A PM role can't reach Jira or Azure Boards unless you
  add an MCP server for them. DevOps prepares files; it doesn't deploy.

## Tips

- **Commit to git before a run.** `/rewind` undoes edits made with Write and
  Edit, not changes made by commands in Bash.
- **If the lead skips a role**, name it in your request: "…then use the tester
  agent".
- **Fix the files, not the chat.** When a role does something wrong, improve
  its `.md` or the skill: every later run gets better.
- **More roles** work the same way: a `reviewer` (tools: Read, Grep, Glob;
  reviews the developer's changes before the tester), a `docs` writer, a
  `security` reviewer. Add each to the process in `CMCODER.md`.

## See also

- [../guides/setup.md](../guides/setup.md): first-time setup (gateway, API key).
- [code-search.md](code-search.md): the project index behind `CodeSearch`.
- [../admin/](../admin/README.md): managed settings, for rules every team must follow.
