# cmcoder documentation

cmcoder is a coding agent in the style of Claude Code, for the terminal and
for VS Code, Visual Studio, Eclipse, NetBeans, IntelliJ IDEA and the other
JetBrains IDEs. It works with your company's AI gateway (LiteLLM or Open
WebUI), and developers' PCs need no Python.

## Start here: who are you?

| You are | You want to | Start with |
|---|---|---|
| **A developer using cmcoder** | install it in your IDE or terminal, set it up, use it | [guides/](guides/README.md) |
| **A team lead** | code search, MCP, hooks, skills, subagents, a whole agent team for your repository | [features/](features/README.md), then [features/team.md](features/team.md) |
| **An administrator (IT)** | roll it out: company settings, managed rules, branding, the release bundle | [admin/](admin/README.md) |
| **A release engineer** | build and pack each package with cmcoder inside, and release them | [build-package/](build-package/README.md) |
| **A new developer on cmcoder itself** | learn the code, run the tests, make a change | [learn/](learn/README.md) |
| **A maintainer** | the design, the phase plans, the security reviews | [project/](project/README.md) |

## What's where

```
docs/
  guides/          install and use: each IDE, the terminal, first-time setup, troubleshooting
  features/        one guide per feature: settings, gateway and models, code search (RAG),
                   MCP, hooks, commands, memory, skills, subagents, the team, permissions,
                   sandbox, critic, sessions, images, usage metrics
  admin/           roll-out: settings template, managed settings, branding, the bundle
  build-package/   build, pack and release each package (one-command scripts per IDE)
  learn/           the learning path: source map, contributing, the phase-by-phase code guides
  project/         DESIGN.md, each phase's plan, status, testing and security review, scans
  examples/        settings, managed settings, a ready-made team folder
```

Each IDE folder in the repository also has a `README.md` for the people who
work on that plugin ([learn/source-map.md](learn/source-map.md)).

## Keeping these docs tidy

- Put a page where its reader looks first: using → `guides/`, setting up a
  feature → `features/`, rolling out → `admin/`, building → `build-package/`,
  learning the code → `learn/`, decisions and history → `project/`.
- One topic, one place: link to it instead of repeating it.
- `tests/test_docs_links.py` fails on a dead link or `#section`, so moving a
  page means fixing its links in the same change.
- Example addresses only (`example.com`, `<your repository URL>`), and never a
  key or token.
