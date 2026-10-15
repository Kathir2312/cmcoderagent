# Project: design, phases and reviews

How cmcoder was planned, built and checked. For the people who maintain it.

- **[DESIGN.md](DESIGN.md)**: the architecture, the decisions and why, the
  roadmap. The one living document: update it when a decision changes.
- **[phases/](#the-phases)**: each phase's plan, status, test notes and
  security review.
- **[security/](security/)**: the full security scans and their triage
  ([scan-2026-10-07.md](security/scan-2026-10-07.md),
  [scan-2026-10-13.md](security/scan-2026-10-13.md)).

The code lives in one place (`src/cmcoder/` and the IDE folders) and grew
phase by phase. Each finished phase is a git tag, and its documents are
frozen in its folder. The guides that explain each phase's code are in
[../learn/](../learn/README.md).

## The phases

| Phase | Status | Snapshot | Documents | Code guides |
|---|---|---|---|---|
| **0: Foundations** | ✅ Complete | commit `ba6669f` (tag `phase0`) | [STATUS](phases/phase0/STATUS.md) | [learn/phase0/](../learn/phase0/python-guide.md) |
| **1: Daily driver** | ✅ Complete | tag `phase1` | [PLAN](phases/phase1/PLAN.md), [STATUS](phases/phase1/STATUS.md) | [learn/phase1/](../learn/phase1/python-guide.md) |
| **2: VS Code** | ✅ Complete | tag `phase2` | [PLAN](phases/phase2/PLAN.md), [STATUS](phases/phase2/STATUS.md), [SECURITY-REVIEW](phases/phase2/SECURITY-REVIEW.md) | [learn/phase2/](../learn/phase2/python-guide.md) |
| **3: Extensibility** | ✅ Complete | tag `phase3` | [PLAN](phases/phase3/PLAN.md), [STATUS](phases/phase3/STATUS.md), [TESTING](phases/phase3/TESTING.md), [SECURITY-REVIEW](phases/phase3/SECURITY-REVIEW.md) | [learn/phase3/](../learn/phase3/python-guide.md) |
| **4: Hardening** | ✅ Complete | commit `e78bdb1` (code) | [PLAN](phases/phase4/PLAN.md), [STATUS](phases/phase4/STATUS.md), [TESTING](phases/phase4/TESTING.md), [SECURITY-REVIEW](phases/phase4/SECURITY-REVIEW.md) | [learn/phase4/](../learn/phase4/python-guide.md) |
| **5: Code search (RAG)** | ✅ Complete | commit `46f2e75` | [PLAN](phases/phase5/PLAN.md), [STATUS](phases/phase5/STATUS.md), [TESTING](phases/phase5/TESTING.md), [SECURITY-REVIEW](phases/phase5/SECURITY-REVIEW.md) | [learn/phase5/](../learn/phase5/python-guide.md) |
| **6: Other IDEs** | ✅ Complete | | [PLAN](phases/phase6/PLAN.md), [STATUS](phases/phase6/STATUS.md), [SECURITY-REVIEW](phases/phase6/SECURITY-REVIEW.md) | [learn/phase6/](../learn/phase6/ide-guide.md) |
| **7: IntelliJ-based IDEs** | ✅ Complete (13 Oct 2026; the guide's screenshots to come) | | [PLAN](phases/phase7/PLAN.md) (with its status) | — |
| **8: The engine on LangChain and LangGraph** | 🚧 In progress (M0 done 15 Oct 2026; M1 under way) | | [PLAN](phases/phase8/PLAN.md), [SPIKE](phases/phase8/SPIKE.md) | [learn/phase8/](../learn/phase8/README.md) |

## What each phase's documents contain

| File | For whom | Contents |
|---|---|---|
| `PLAN.md` | everyone | Scope, order, acceptance checks and a progress checklist. Written when the phase starts. |
| `STATUS.md` | everyone | What was delivered, how it was validated, what changed from the plan. Written when the phase ends. |
| `TESTING.md` | testers | Hands-on test steps (phase 3 has a script that makes a test project: [try-phase3.ps1](phases/phase3/try-phase3.ps1)). |
| `SECURITY-REVIEW.md` | maintainers, security | What the phase could expose, and what was done about it. |
| `../learn/phaseN/python-guide.md` | Python developers | The phase's code explained step by step. |
| `../learn/phaseN/langgraph-guide.md` | LangChain / LangGraph developers | The phase's features mapped to their LangChain/LangGraph equivalents. |

## The code as it was at the end of a phase

From GitHub: open the repository, switch to the tag (e.g. `phase0`) and use
**Code → Download ZIP**. With git:

```bash
git fetch --tags
git checkout phase0        # read-only snapshot; `git switch -` to go back
```
