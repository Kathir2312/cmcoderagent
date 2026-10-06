# Phase 6 — Other IDEs: status

**Status:** ✅ complete (marked complete on 6 October 2026, the user's
decision after the final release build passed). **Snapshot:** commit
`bfa09d2`, release build #63 and CI #153 green on Windows, macOS and Linux.

Phase 6's goal ([PLAN.md](PLAN.md)): *developers who use Visual Studio 2022,
Eclipse or Apache NetBeans get cmcoder inside their IDE, with the same chat
panel and features as VS Code, on PCs that have no Python.* Met: every file
is built by CI, carries its own `cmcoder`, and passed the release gate in the
real IDE with no Python, uv, pip or Node.js in reach.

## Delivered

| Item | What | Where |
|---|---|---|
| 1. No Python anywhere | Terminal package with install scripts, the no-Python test environment (`packaging/no_python.py`), `--client`, Git Bash check (D16) | `packaging/`, [guides/terminal.md](../guides/terminal.md) |
| 2. One chat panel | The shared web page every IDE shows (VS Code included), its bridge and test bridge; sanitized Markdown | `clients/web-panel` |
| 3. Protocol classes | Java and C# classes checked against the schema | `clients/jvm-core`, `visualstudio/src/Cmcoder.Core` |
| 4. JVM core | Program lookup, process, JSON lines, executable bit, the host logic (shared by Eclipse and NetBeans) | `clients/jvm-core` |
| 5. JetBrains | **Dropped** (the user's decision, 6 October) | — |
| 6. Eclipse plugin | Chat view, compare editor review, context, diagnostics, navigator, code search, preferences; update-site zip for every platform | `eclipse/`, [guides/eclipse.md](../guides/eclipse.md) |
| 7. Visual Studio 2022 | WebView2 chat, diff window with an info bar, job object for cmcoder, the same features | `visualstudio/` |
| 8. Release workflow | Per-platform builds and gates, gate report, release bundle with `README-FIRST.txt` and `SHA256SUMS.txt`, GitHub release on `v*` tags | `.github/workflows/release.yml`, `packaging/bundle.py` |
| 9. Security review | Findings fixed (per-page-load secret for the NetBeans bridge, test driver only in test builds, OSV check of OpenJFX and NuGet) | [SECURITY-REVIEW.md](SECURITY-REVIEW.md) |
| 10. Docs | Guides per IDE, setup, admin, code search, troubleshooting; code guides | [docs/guides/](../guides/README.md), [ide-guide.md](ide-guide.md) |
| 11. Apache NetBeans | JavaFX WebView chat, Diff API review, error providers, Options, one `.nbm` per platform for NetBeans 28–31 | `netbeans/`, [guides/netbeans.md](../guides/netbeans.md) |

Also in this phase: Chroma is reached only by its URL (D17), the chat header
puts the model name on its own row in narrow panels, and Eclipse's chat
opens on the right with the diff not mirrored (all from the gate screenshots).

## Validation (release build #63, commit `bfa09d2`)

| Platform | Gates passed, each in the real IDE with the bundled `cmcoder` and no Python |
|---|---|
| Windows x64 | standalone build, IDE cores (JVM and .NET), Eclipse, NetBeans 31, **Visual Studio 2022**, terminal package, VS Code package |
| macOS arm64 | standalone build, IDE cores, Eclipse, NetBeans 31, terminal package, VS Code package |
| Linux x64 | standalone build, IDE cores, Eclipse, NetBeans 31, terminal package, VS Code in a real VS Code |

CI (#153) adds the Python and panel tests on every platform, Eclipse 2024-06
and newest, NetBeans 28 (JDK 21) and 31 (JDK 25), and the security job
(Bandit, pip-audit, npm audit, OSV). The **release bundle** job assembled
every file for the first time in this build (`cmcoder-release-bundle`, with
the gate report).

Files in the bundle:

- `cmcoder-terminal-<platform>.zip`;
- `cmcoder-<platform>.vsix` (VS Code);
- `cmcoder-visualstudio.vsix`;
- `cmcoder-eclipse` (the update site);
- `cmcoder-netbeans-<platform>.nbm`;

The platforms are win32-x64, darwin-arm64 and linux-x64.

Screenshots: [Eclipse](../guides/eclipse.md) and
[NetBeans](../guides/netbeans.md) in the guides. Visual Studio's are in the
run's `gate-screenshots-win32-x64` artifact.

## Changed from the plan

- **JetBrains dropped**, **NetBeans added** (item 11), both decided 6 October.
- **NetBeans gate installs by unpacking the `.nbm`** into a fresh user folder:
  NetBeans' own `--modules --install` refreshes the update catalog first,
  which closed networks refuse.
- **NetBeans bridge** trusts messages by a secret per page load, not by the
  page's address (found by the gate; see the security review).
- **Chroma by URL only** (D17): no Chroma inside the program.

## Carried forward

- **Code signing**: the files are unsigned until the company provides a
  certificate and a publisher name; SmartScreen, Gatekeeper and Eclipse warn
  until then (the guides say what to do). The release workflow has the slot.
- **Hands-on use on your machines**: the gates use a mock model server; the
  IDE plugins haven't yet been used against your real gateway.
- **Chroma on this machine**: to be set up later (D17).
- Watch: a one-off macOS Eclipse "first reply" timeout (run #53; not seen
  since).
- Minor: the 📎 emoji shows as a box under Xvfb only (no emoji font there).
