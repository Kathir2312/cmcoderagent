# cmcoder documentation

The code lives in one place (`src/cmcoder/`) and grows phase by phase. Each
finished phase is frozen as a git snapshot, and its documents are frozen in
its own folder. The current phase's documents are updated as features land.

| Phase | Status | Snapshot | Documents |
|---|---|---|---|
| **0 — Foundations** | ✅ Complete | commit `ba6669f` (tag `phase0`) | [phase0/](phase0/) |
| **1 — Daily driver** | ✅ Complete | tag `phase1` | [phase1/](phase1/) |
| **2 — VS Code** | ✅ Complete | tag `phase2` | [phase2/](phase2/) |
| **3 — Extensibility** | ✅ Complete | tag `phase3` | [phase3/](phase3/) |
| 4 — Hardening | Planned | — | — |

The overall design, decisions and roadmap stay in one living document:
[DESIGN.md](DESIGN.md).

## What each phase folder contains

| File | For whom | Contents |
|---|---|---|
| `PLAN.md` | everyone | Scope, order, acceptance checks and a progress checklist. Written when the phase starts. |
| `python-guide.md` | Python developers (from day 1) | The phase's code explained step by step. |
| `langgraph-guide.md` | LangChain / LangGraph developers | The phase's features mapped to their LangChain/LangGraph equivalents. |
| `STATUS.md` | everyone | What was delivered, how it was validated, what changed from the plan. Written when the phase ends. |

## Getting the code as it was at the end of a phase

From GitHub: open the repository, switch to the tag (e.g. `phase0`) and use
**Code → Download ZIP**. With git:

```bash
git fetch --tags
git checkout phase0        # read-only snapshot; `git switch -` to go back
```

## Settings example

[settings.example.json](settings.example.json) shows every setting the
current code understands; [managed-settings.example.json](managed-settings.example.json)
is a starting point for administrators.
