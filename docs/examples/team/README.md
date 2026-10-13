# A ready-made team: copy into your project

The files of [../../features/team.md](../../features/team.md), ready to copy:
a team lead with four roles (PM, developer, tester, DevOps), two skills, the
`/feature` command, shared rules and a quality-gate hook.

```
CMCODER.md                                the team process (the lead reads it every session)
mcp.example.json                          a ticket system over MCP (optional: rename to .mcp.json)
.cmcoder/
  settings.json                           shared allow rules and a hook (adjust to your build)
  agents/      pm.md  developer.md  tester.md  devops.md
  skills/      coding-standards/SKILL.md  testing-guide/SKILL.md
  commands/    feature.md                 /feature runs the whole flow
```

## Use it

1. Copy everything in this folder (including the hidden `.cmcoder` folder)
   into your project's root. If the project has a `CMCODER.md` already, add
   this one's section to it.

   ```
   # Windows (Command Prompt), from this repository's root
   xcopy /E /I docs\examples\team\.cmcoder <your project>\.cmcoder
   copy docs\examples\team\CMCODER.md <your project>\

   # macOS, Linux
   cp -R docs/examples/team/.cmcoder docs/examples/team/CMCODER.md <your project>/
   ```

2. Fill in the `...` in the two skills with your real rules.
3. Change `.cmcoder/settings.json` to your build: the example uses
   `dotnet build`, `dotnet test` and `dotnet format`.
4. Optional: a ticket system over MCP: copy `mcp.example.json` to
   `<your project>/.mcp.json` and change the URL (each person sets
   `JIRA_TOKEN` in their environment).
5. Optional: code search for big projects: `cmcoder rag setup` in the project
   ([../../features/code-search.md](../../features/code-search.md)).
6. Trust the project, check it (`cmcoder doctor`) and run
   `/feature <what to build>`: steps 9 to 11 of
   [../../features/team.md](../../features/team.md#step-9-trust-the-project-and-give-the-lead-room).

Commit the files: everyone on the repository then has the same team.
