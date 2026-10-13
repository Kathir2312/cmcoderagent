# Phase 7 — IntelliJ-based IDEs: plan

**Status:** complete (13 October 2026), with two open points below. The
plugin builds and passes the Plugin Verifier on 2024.2, the newest IntelliJ
IDEA and the newest Rider. It passes the release gate in a real IntelliJ IDEA
on Windows, macOS and Linux (in the release build, each platform's cmcoder
inside) and in PyCharm. It is in the release bundle and has passed its
security review ([../security/scan-2026-10-13.md](../security/scan-2026-10-13.md)).
Its guide is [../guides/intellij.md](../guides/intellij.md).

Open points:

- The guide's screenshots (`docs/guides/screenshots/intellij-1-chat.png`,
  `-2-diff`, `-3-navigator`) are to be committed from the release build's
  `gate-screenshots-*` artifact; this environment can't download artifacts.
- Rider: the Plugin Verifier only (decided 13 Oct). Rider needs a license
  to start, so it doesn't run the live gate. Phase 6 is complete ([../phase6/STATUS.md](../phase6/STATUS.md)); this
phase adds the IDE family Phase 6 dropped.

> **Note: developers' PCs may not have Python installed** (nor uv, pip or
> Node.js). The plugin carries its own `cmcoder` and must work without them;
> see [DESIGN.md](../DESIGN.md) decision D16.

**Goal:** developers who use **IntelliJ IDEA** or another **IntelliJ-based
IDE** (Rider, PyCharm, WebStorm, GoLand, Android Studio, …) get cmcoder inside
their IDE, with the same chat panel and features as VS Code, Visual Studio,
Eclipse and NetBeans, on PCs that have **no Python**.

**Done when:** the plugin is built by CI as a file per platform, installs on a
clean PC without Python, Node or uv ("Install Plugin from Disk"), and passes
the **release gate** (Phase 6's G-list) in a real IntelliJ IDEA on Windows,
macOS and Linux, against the cmcoder bundled in that file, plus a smoke run in
Rider and PyCharm, and the JetBrains Plugin Verifier finds no problem from
2024.2 to the newest release. The gate report and the release bundle include
it.

Phase 6's three strictness rules apply unchanged (the files go to all
developers at once): **no feature without a gate scenario**, **test what
developers install** (the built plugin zip, installed into a fresh IDE
profile), and **no Python on the search path** during the gate.

## Decisions (12 October 2026)

| Question | Decision |
|---|---|
| Which IDEs | **All IntelliJ-based IDEs, one plugin**: IntelliJ IDEA Community and Ultimate, Rider, PyCharm, WebStorm, GoLand, PhpStorm, CLion, RubyMine, Android Studio. The plugin depends on the platform only (`com.intellij.modules.platform`), nothing language-specific. |
| Oldest version | **2024.2** (`sinceBuild` 242), no upper bound unless the Plugin Verifier finds a break. |
| Where it's built and tested | **GitHub CI only.** JetBrains' servers are still blocked by this cloud environment's network policy (checked 12 October: plugins.jetbrains.com, cache-redirector.jetbrains.com, download.jetbrains.com, www.jetbrains.com, packages.jetbrains.team all refused), so nothing IntelliJ-specific builds here; CI on Windows, macOS and Linux does it all. |
| Scope | **The plugin only.** The thinking switch, the security P1 fixes and the code-search read-only fixes stay separate. |
| How developers get it | As in Phase 6: **files only**, `cmcoder-jetbrains-<platform>.zip`, installed with Settings → Plugins → ⚙ → *Install Plugin from Disk…*. No plugin repository or update server. |
| Language and build | **Java**, like the JVM core and the Eclipse and NetBeans plugins (one language for every JVM plugin; the core's sources are compiled into the plugin). **Gradle** with the IntelliJ Platform Gradle Plugin 2.x (the only supported build for IntelliJ plugins). Java 21 bytecode, what 2024.2 and newer run on. |

## What gets delivered

| File | For | Platforms | Install |
|---|---|---|---|
| `jetbrains/cmcoder-jetbrains-<platform>.zip` | Every IntelliJ-based IDE, **2024.2 or newer** | win32-x64, linux-x64, darwin-arm64 | Settings → Plugins → ⚙ → *Install Plugin from Disk…* |

Each zip holds the plugin, the JVM core, the shared chat panel and that
platform's standalone `cmcoder` (`bin/cmcoder/`). As the other plugins do
since 11 October, the plugin's copy updates the shared one in
`%USERPROFILE%\.cmcoder\bin` when it's newer, and the plugin runs the shared
copy when it's at least as new (`ProgramLocator`).

## How it fits together

```
IntelliJ-based IDE
 └─ cmcoder plugin (jetbrains/, Java)
     ├─ tool window "cmcoder": the shared chat page in JCEF (JBCefBrowser)
     ├─ IntelliJIde implements cmcoder.ide.core.Ide  ← editor, diff, files, settings
     └─ cmcoder.ide.core.Host (clients/jvm-core, shared with Eclipse and NetBeans)
           └─ cmcoder --protocol stdio --client jetbrains  (bundled or shared copy)
```

Everything the host does that isn't IntelliJ-specific (relaying, permissions,
IDE tools, rewind, history, links, the navigator's replay, program lookup and
the process) is already in the JVM core and its tests. The plugin adds only
what needs IntelliJ's APIs.

## Host duties with IntelliJ's APIs

Phase 6's host contract (H1–H24) with the API each uses:

| ID | Duty | IntelliJ |
|---|---|---|
| H1–H4 | Find, start, stop the program; failures with **Restart** | JVM core (`ProgramLocator`, `AgentProcess`, `Host`); one process per open project, stopped by a `Disposable` when the project closes |
| H5, H18, H19 | The chat page, messages both ways, links, panel security | `JBCefBrowser`; the page's files from the plugin through a scheme handler (never `file://` or the network); page → plugin through `JBCefJSQuery` behind `window.cmcoderHostPost`, with a secret per page load (as NetBeans); plugin → page with `executeJavaScript` (`window.postMessage`); navigation away refused (`CefRequestHandler`), pop-ups refused, the context menu and dev tools off in release builds; `http(s)` links to `BrowserUtil.browse` |
| H6 | `system_init`, capabilities, protocol check | JVM core |
| H7 | Editor context | `FileEditorManager`, `Editor.getSelectionModel()`, the open file's highlights (`DaemonCodeAnalyzerImpl.getHighlights`, or `WolfTheProblemSolver` where the internal API isn't allowed) |
| H8 | IDE tools `getDiagnostics`, `openFile` | Highlighting of open files (as H7); `OpenFileDescriptor` at a line |
| H9, H10 | Changes in the IDE's diff viewer, Accept / Reject there | `DiffManager` with a `SimpleDiffRequest` in an editor tab, Accept / Accept Always / Reject actions in its toolbar |
| H11 | `/rewind` pickers | `JBPopupFactory` lists and a dialog |
| H12 | History, resume, new conversation | JVM core + toolbar actions |
| H13 | Attach a file (@) | `FileChooser` limited to the project |
| H14 | Ask About Selection | Editor context-menu action and a shortcut |
| H15 | Agent Navigator | An editor tab (`FileEditorProvider` for a light virtual file) with the navigator page in JCEF |
| H16 | Code search | A status bar widget; the set-up as dialogs (`DialogWrapper`), as in Eclipse and NetBeans |
| H17 | Settings page | `Configurable` + `PersistentStateComponent` (application level: a project can't set the program) |
| H20 | Themes | `LafManagerListener`; the page gets the IDE's colours (`JBColor`) as CSS variables, live |
| H21 | Project trust | The IDE's own trusted projects (`TrustedProjects`): `--trust-project` only for a trusted project, as VS Code with its workspace trust. The only way to ask is an experimental API, deprecated in 2026.2 but not scheduled for removal; the verifier fails the build if it ever is |
| H22 | Log, Copy Diagnostics | A log tool window tab; Copy Diagnostics to the clipboard (never the key) |
| H23 | Telemetry label | `--client jetbrains` |
| H24 | Branding | `plugin.xml`'s name, vendor and icons from `branding/` at build time (as `vsix.py` and the NetBeans build do) |

If JCEF isn't available (some remote-development or custom runtimes), the
tool window says so and how to fix it (switch to a JetBrains Runtime with
JCEF), and the terminal still works.

## The release gate

The gate runs Phase 6's scenarios (G1–G23) in a **real IntelliJ IDEA**, on the
oldest (2024.2) and newest releases, through the IDE and the real JCEF page.
As for NetBeans and Visual Studio, the gate is a **self-test compiled only
into a test build** (`-Pgate`), started when the IDE runs with
`-Dcmcoder.gate=<report file>`: the built zip is unpacked into a fresh plugins
folder exactly as *Install Plugin from Disk* lays it out, the mock model server
answers, Python is off the search path, Xvfb on Linux. It drives the page with
the same `test-driver.js` the other gates use and writes which checks passed.

Plus:
- **Plugin Verifier** (`verifyPlugin`) against IntelliJ IDEA Community and
  Ultimate, Rider, PyCharm, WebStorm and GoLand, from 2024.2 to the newest
  release: no missing, internal or deprecated-for-removal APIs.
- **The same gate in PyCharm** (newest) in CI. Rider can't start without a
  license (it shows its license dialog and closes), so the Plugin Verifier
  checks it instead.
- Screenshots from the gate (chat, a diff, the navigator) for the guide.

## Items, in order

1. **Skeleton and pipeline.** `jetbrains/` (Gradle, `plugin.xml`, an empty
   tool window), the JVM core's sources compiled in, a CI job that builds the
   plugin and runs the Plugin Verifier on Linux. Proves the build before any
   feature.
2. **The chat.** The tool window with the shared page in JCEF, the host wired
   (H1–H6, H18–H20, H23), the bundled `cmcoder` per platform with the
   executable bit and macOS quarantine handled, the shared copy. One process
   per project, stopped with the project.
3. **The editor.** Context, IDE tools, the diff review with Accept / Reject,
   rewind pickers, attach file, Ask About Selection (H7–H14).
4. **The rest of the host.** Agent Navigator tab, code search widget and
   set-up dialogs, settings page, project trust, log and Copy Diagnostics,
   branding (H15–H17, H21, H22, H24).
5. **The gate.** The self-test build, run in CI on Linux (2024.2 and newest)
   and in the release build on Windows, macOS and Linux with that platform's
   `cmcoder`; the gate in PyCharm; the Plugin Verifier matrix with Rider.
6. **Release.** The zips in the release bundle (`jetbrains/`), GATE-REPORT
   columns, README-FIRST, SHA256SUMS; the version in `tests/test_versions.py`;
   osv-scanner sees the Gradle dependencies (dependency locking).
7. **Security review.** JCEF hardening checked (no remote content, navigation
   and pop-ups refused, messages only from the plugin's page, no dev tools in
   release), Semgrep and FindSecBugs on the new code, the report updated.
8. **Docs.** `docs/guides/intellij.md` (install, first-time setup, use, with
   the gate's screenshots, settings, updating, fixes); the guides' index,
   README, admin guide, troubleshooting.

## Risks

| Risk | Handling |
|---|---|
| Nothing builds here (JetBrains blocked) | Every build and test in CI; small steps, each pushed and checked; item 1 first proves the pipeline. Adding JetBrains' domains to this environment's allow-list would let it build here too. |
| JCEF missing in some runtimes | Detected (`JBCefApp.isSupported()`), with a clear message; guide covers it |
| API changes from 2024.2 to the newest | Plugin Verifier on every CI run; public APIs only |
| Big IDE downloads in CI | Downloaded per run (no caches in the release build, as Phase 6 decided), checked against JetBrains' published checksums |
| JCEF in CI (Windows, headless Linux) | Off-screen rendering where needed, Xvfb on Linux; the NetBeans and Visual Studio gates solved the same class of problem |
| Remote development (Gateway, thin client) | Not supported in this phase: the plugin runs where the IDE's backend runs; stated in the guide |

## Checklist

- [x] 1. Skeleton, CI build, Plugin Verifier (12 Oct: CI builds the plugin
  with Gradle 9.8.1 and the IntelliJ Platform Gradle Plugin 2.19.0; the
  verifier finds it compatible with IntelliJ IDEA 2024.2.5 and 2026.2, no
  internal API, and it can be enabled without a restart. Found by the
  verifier: the plugin manager's lookups are internal API in 2026.2, so the
  plugin finds its folder from its own jar.)
- [x] 2. Chat in JCEF, host, bundled and shared `cmcoder` (code in, builds
  and verifies; checked in a real IDE with the gate)
- [x] 3. Editor duties (H7–H14) (code in, builds and verifies on 2024.2 and
  2026.2; checked in a real IDE with the gate)
- [x] 4. Navigator, code search, settings, trust, diagnostics, branding
  (code in, builds and verifies on 2024.2 and 2026.2; checked in a real IDE
  with the gate. The navigator is an editor tab; code search a status bar
  item with the set-up as dialogs; Settings → Tools → cmcoder; a Log tab and
  Copy Diagnostics; Tools → cmcoder menu. The brand's name fills plugin.xml's
  name, vendor and menu texts, and its one-colour icon becomes the plugin,
  tool window and tab icons; the build checks the jar for an unfilled name.)
- [x] 5. Gate in CI and the release build; PyCharm; Rider by the verifier
  (12 Oct: **the gate passes in CI on Linux in IntelliJ IDEA 2024.2.5 and in
  the newest, 2026.2.3** (looked up from JetBrains' release list each run):
  the gate zip unpacked into a fresh profile as *Install Plugin from Disk*
  does, the cmcoder inside it, the mock model server, Xvfb, no Python in
  reach; 25 steps from the chat page and editor context to the diff window,
  the navigator and cmcoder stopping with the chat. It found two bugs the
  build and the Plugin Verifier hadn't: cmcoder refused `--client jetbrains`,
  and in 2026.2 JCEF is a module of its own that the plugin must declare (an
  optional dependency now; without it the chat wouldn't open there). The
  newest IDE also shows a modal onboarding dialog on a fresh profile, which the
  gate closes and records. When a step stalls, the report lists the windows
  on screen and the runner prints the UI thread. Then **on Windows and macOS
  too** (newest IntelliJ IDEA, CI), and **in the release build on all three**:
  each platform's job builds `cmcoder-jetbrains-<platform>.zip` with that
  platform's cmcoder inside, then the gate build with the same cmcoder, and
  runs the gate with no Python in reach; the gate report lists it.
  `gate/get-ide.sh` fetches any JetBrains IDE by product code and version from
  JetBrains' release list, checked against the published checksum.
  **PyCharm** (newest) passes all 25 steps; PyCharm itself runs `uv python dir`
  to look for interpreters, which the no-Python check now records as a note
  when the IDE's own process starts it (`--ide-looks-for-python`, PyCharm
  only, POSIX only: the plugin's code runs in that process too, so IntelliJ
  IDEA's gate still fails on it). **Rider** needs a license to start (on a
  fresh profile it shows the license dialog and closes), so the Plugin
  Verifier checks the newest Rider instead; a license kept as a CI secret
  would let the gate run there too.)
- [x] 6. Release bundle, versions, dependency scanning (13 Oct: the bundle
  has `jetbrains/cmcoder-jetbrains-<platform>.zip`, and there is no bundle
  without all three. README-FIRST says how to install it in any JetBrains
  IDE. The gate report lists the release build's IntelliJ check and what
  isn't tested: 2024.2 only on Linux, PyCharm in CI, Rider by the verifier.
  The version is in `tests/test_versions.py`. Gradle dependency locking:
  `gradle.lockfile` shows the plugin ships no third-party library, and
  `buildscript-gradle.lockfile` pins the build's 73 tools for osv-scanner.
  CI fails if the lock files aren't current.)
- [x] 7. Security review (13 Oct, [scan-2026-10-13.md](../security/scan-2026-10-13.md)).
  The JCEF page's hardening was checked against the code and the gate.
  SpotBugs + FindSecBugs now run in CI, failing on any security finding not
  reviewed: of 11, one was fixed and 10 are listed with reasons in
  `jetbrains/spotbugs/reviewed.xml`. osv-scanner found vulnerable jackson
  and jsoup in the build's tools (fixed by pinning) and an undertow flaw
  with no fix, which builds can't reach (skipped until 2027-01-13).
- [x] 8. Docs (13 Oct). `docs/guides/intellij.md` covers what's needed,
  the file per platform, installing from disk, first-time setup, the chat,
  reviewing a change, everyday actions, settings, project trust, updating
  and fixes. The guides' index, setup, images, troubleshooting, admin guide
  and README name it. The gate also checks that Ask About Selection's
  shortcut belongs to no other action in the IDE's keymap: it found Ctrl+Alt+K
  is Commit and Push in JetBrains IDEs, so the plugin uses Ctrl+Alt+Shift+K
  there (decided 13 Oct). Screenshots: the guide links them; they are added
  from the release build's `gate-screenshots-*` artifact (see Status).
