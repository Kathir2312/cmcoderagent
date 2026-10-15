# Learning path for new developers

cmcoder was built in phases, and each phase has a guide that explains its
code step by step. Read them in order: each one builds on the last. Pick the
track that matches what you know.

## Week one

| Step | Read or do | Time |
|---|---|---|
| 1 | **Use it.** Install it and set it up like a user ([../guides/setup.md](../guides/setup.md), [../guides/terminal.md](../guides/terminal.md)), then ask it about this repository | 1 hour |
| 2 | **The map.** [source-map.md](source-map.md): the parts, how they talk, where to start reading | 30 min |
| 3 | **Run the checks.** [contributing.md](contributing.md): set up, run the tests and the mock evals | 1 hour |
| 4 | **The phases, in order** (below), in your track | a few days |
| 5 | **The design.** [../project/DESIGN.md](../project/DESIGN.md): the decisions and why | 2 hours |
| 6 | **The features as users see them.** [../features/](../features/README.md): set up MCP, a hook, a skill and a subagent in a test project; then the team ([../features/team.md](../features/team.md)) | half a day |
| 7 | **A first change.** A small fix with its test, following [contributing.md](contributing.md#adding-or-changing-a-feature) | — |

## The phases

| Phase | What it added | Python track | LangChain / LangGraph track |
|---|---|---|---|
| 0: Foundations | the agent loop, the tools, the provider, permissions | [phase0/python-guide.md](phase0/python-guide.md) | [phase0/langgraph-guide.md](phase0/langgraph-guide.md) |
| 1: Daily driver | sessions, compaction, the terminal UI, hooks basics, context window | [phase1/python-guide.md](phase1/python-guide.md) | [phase1/langgraph-guide.md](phase1/langgraph-guide.md) |
| 2: VS Code | the Agent Protocol and the VS Code extension | [phase2/python-guide.md](phase2/python-guide.md) | [phase2/langgraph-guide.md](phase2/langgraph-guide.md) |
| 3: Extensibility | MCP, hooks, slash commands, subagents, skills, trust | [phase3/python-guide.md](phase3/python-guide.md) | [phase3/langgraph-guide.md](phase3/langgraph-guide.md) |
| 4: Hardening | the sandbox, the standalone build, Open WebUI, branding | [phase4/python-guide.md](phase4/python-guide.md) | [phase4/langgraph-guide.md](phase4/langgraph-guide.md) |
| 5: Code search | RAG over the project: embeddings, the index, `CodeSearch` | [phase5/python-guide.md](phase5/python-guide.md) | [phase5/langgraph-guide.md](phase5/langgraph-guide.md) |
| 6: Other IDEs | one chat page, the JVM and .NET hosts, Visual Studio, Eclipse, NetBeans | [phase6/python-guide.md](phase6/python-guide.md) | [phase6/langgraph-guide.md](phase6/langgraph-guide.md) |
| 7: IntelliJ-based IDEs | the JetBrains plugin on the same JVM core | — (Java: the IDE track) | — |
| 8: The engine on LangChain and LangGraph (in progress) | the engine rewritten on LangChain, LangGraph, LangSmith and Langfuse, milestone by milestone, each with a lab | — (the LangChain track is the main one here) | [phase8/](phase8/README.md) |

**IDE track (Java, C#, TypeScript):** [phase6/ide-guide.md](phase6/ide-guide.md)
explains how the plugins are built: one chat page in each IDE's browser, one
host per platform, the gates. Then the plan of phase 7
([../project/phases/phase7/PLAN.md](../project/phases/phase7/PLAN.md)) for
the JetBrains plugin, which reuses the JVM core.

**Release track:** [../build-package/](../build-package/README.md), then
[../admin/](../admin/README.md).

## Reading an older guide

Each guide was written when its phase ended and shows the code as it was
then; later phases changed some of it. To read the code beside the guide, check
out the phase's tag:

```bash
git fetch --tags
git checkout phase3        # read-only; `git switch -` to come back
```

Each phase's plan, status and security review (what was built, how it was
checked, what changed from the plan) are in
[../project/](../project/README.md).
