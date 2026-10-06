# Phase 6 — Other IDEs: plan

**Status:** 🚧 planned (6 October 2026). Phase 5 ended at commit `46f2e75`.

> **Note: developers' PCs may not have Python installed** (nor uv, pip or
> Node.js). Every file in this phase carries its own `cmcoder` and must work
> without them; see [DESIGN.md](../DESIGN.md) decision D16.

**Goal:** developers who use **JetBrains IDEs** (IntelliJ IDEA, Rider,
PyCharm, WebStorm, ...), **Visual Studio 2022** or **Eclipse** get cmcoder
inside their IDE, with the same chat panel and features as VS Code, on PCs
that have **no Python**.

**Done when:** every deliverable is built by CI as a file, installs on a
clean PC without Python, Node or uv, and passes the **release gate**: the same
scenarios run automatically in each real IDE (on Windows, and on macOS and
Linux where the IDE runs there), against the cmcoder program bundled in that
file. The gate's report is published with the files.

## Why this plan is strict

The files go **directly to all developers** (decision below): nobody tries
them by hand first, and there is no pilot group. So everything a person would
normally check by clicking around must be checked by an automated test in the
real IDE, and anything a test can't cover must be called out in the release
notes. Three rules follow, used in every item:

1. **No feature without a gate scenario** (the G-list below) that exercises it
   in each IDE, through the IDE's own UI and the real embedded browser.
2. **Test what developers install**: the gate installs the built file
   (plugin `.zip`, `.vsix`, update-site archive) into the IDE, not the source
   folder. (The empty Agent Navigator of 6 October happened because tests
   loaded files from the build folder instead of the package.)
3. **No Python anywhere on the test machine's search path** during the gate,
   so a hidden dependency on Python fails the build instead of a developer.

## Decisions (6 October 2026)

| Question | Decision |
|---|---|
| Which IDEs | **All three**: JetBrains (one plugin for IntelliJ-based IDEs), **Visual Studio 2022**, **Eclipse**. |
| How developers get them | **Files only.** CI builds the files; developers install them from disk. Updates are a new file. No plugin repository or update server. |
| Rollout | **Everyone at once.** The build that passes the release gate goes to all developers. Hence the strictness above, and a "Copy diagnostics" command in every IDE so a problem report needs no back-and-forth. |
| Python on developers' PCs | **None.** Every file carries the standalone `cmcoder` program (Phase 4's PyInstaller build). No plugin ever falls back to Python, `uv` or `pip`. |

Carried over: JetBrains' servers are blocked by this cloud environment's
network policy, so the JetBrains plugin is built and tested in GitHub CI
(which has internet access). Adding `plugins.jetbrains.com`,
`cache-redirector.jetbrains.com`, `download.jetbrains.com`,
`download-cdn.jetbrains.com`, `www.jetbrains.com` and `packages.jetbrains.team`
(the build tool's libraries) to the environment's
allowed domains would let it build here too. Eclipse (download.eclipse.org,
Maven Central) and NuGet are reachable.

## What a developer's PC needs

| | Needed | Not needed |
|---|---|---|
| All | Windows 10/11 x64 (or macOS arm64, Linux x64 for JetBrains, Eclipse, VS Code); the gateway's address and an API key (as today) | **Python, uv, pip, Node.js, npm** |
| Shell commands (the Bash tool) on Windows | **Git for Windows** (Git Bash), as for cmcoder today. Without it cmcoder still starts and answers; only shell commands fail, with a message saying what to install. `doctor` checks it. | |
| Visual Studio, Eclipse on Windows | The **Microsoft Edge WebView2 Runtime** (part of Windows 11 and of current Windows 10; Visual Studio installs it). The panel says so if it's missing. | |
| Eclipse | Java 21 (the JRE that comes with current Eclipse packages) | |
| Optional | ripgrep (faster search on big repositories), as today | |

Things that stay the team's choice and may need other runtimes: MCP servers
and hooks configured to run `python`, `node` or `npx` need those programs.
The developer guide says so; cmcoder's own features don't.

## What gets delivered

One CI run (the release workflow, on a version tag or by hand) produces a
**release bundle**, a folder of files with one version number:

| File | For | Platforms | Installs with |
|---|---|---|---|
| `jetbrains/cmcoder-jetbrains-<platform>.zip` | IntelliJ IDEA, Rider, PyCharm, WebStorm, GoLand, PhpStorm, CLion, Android Studio, **2024.2 or newer** | win32-x64, linux-x64, darwin-arm64 | Settings → Plugins → ⚙ → *Install Plugin from Disk…* |
| `visualstudio/cmcoder-visualstudio-win32-x64.vsix` | **Visual Studio 2022 17.10 or newer** (Community, Professional, Enterprise) | Windows x64 | double-click the `.vsix` (VSIX Installer), with Visual Studio closed |
| `eclipse/cmcoder-eclipse-<platform>.zip` | Eclipse **2024-06 (4.32) or newer** and Eclipse-based IDEs (Spring Tools, ...) | win32-x64, linux-x64, darwin-arm64 | Help → *Install New Software…* → Add → *Archive…* |
| `vscode/cmcoder-<platform>.vsix` | VS Code (exists since Phase 4) | win32-x64, linux-x64, darwin-arm64 | Extensions → ⋯ → *Install from VSIX…* |
| `terminal/cmcoder-<platform>.zip` | the terminal (`cmcoder`, `cmcoder --tui`), no IDE | win32-x64, linux-x64, darwin-arm64 | unzip, run `install.ps1` / `install.sh` (adds it to the user's PATH; no admin rights) |
| `README-FIRST.txt` | everyone | | which file is for which IDE, and the two-line install for each |
| `GATE-REPORT.md` | you, support | | every gate scenario × IDE × OS with its result |
| `SHA256SUMS.txt` | security | | checksums of every file |

Each IDE file contains the same standalone `cmcoder` for its platform (about
60–100 MB), so the IDE plugins work without the terminal install. The plain
`cmcoder.vsix` from the CI workflow (without a program inside) remains a
developer-only artifact and is **not** in the bundle: it would need Python.

## How it fits together

```
           ┌───────────────── one shared chat panel (HTML/TypeScript) ──────────────────┐
           │  chat · permission cards · todo · agent map · navigator · /commands · ...  │
           └──────▲──────────────────▲───────────────────▲──────────────────▲───────────┘
          postMessage          JBCefJSQuery        BrowserFunction    WebView2 WebMessage
           ┌──────┴─────┐   ┌───────┴───────┐   ┌───────┴──────┐   ┌───────┴─────────┐
           │  VS Code   │   │  JetBrains    │   │   Eclipse    │   │ Visual Studio   │
           │ TypeScript │   │  Kotlin       │   │   Java       │   │ C#              │
           └──────┬─────┘   └───────┬───────┘   └──────┬───────┘   └───────┬─────────┘
                  │                 └── shared JVM core ┘                   │
                  └──────────── JSON lines over stdin/stdout ───────────────┘
                                 cmcoder --protocol stdio (bundled, no Python)
```

- **The engine doesn't change.** Agent, tools, permissions, sandbox, MCP,
  subagents, critique, code search and settings all stay in `cmcoder`. The
  plugins display and relay, so behaviour is the same in every IDE.
- **One chat panel for all four IDEs.** The VS Code panel (`vscode/src/webview`,
  `vscode/src/navigator`) moves to a shared package that talks to its host
  through a small bridge (send, receive, theme). Each IDE shows it in its own
  embedded browser: JCEF (JetBrains), SWT's Edge browser (Eclipse), WebView2
  (Visual Studio). Fixes to the panel reach every IDE at once.
- **One "host contract"**: the list of what every IDE side must do (start the
  program, relay messages, open diffs, answer IDE tool requests, ...), each
  duty numbered (H1–H24 below). Each plugin's tests name the duties they
  cover; CI fails if a duty has no test in some IDE.
- **One JVM core** for JetBrains and Eclipse (both run on Java): starting and
  stopping `cmcoder`, reading JSON lines, finding the program safely, the few
  protocol messages a host must understand. Plain Java, no IDE classes,
  tested on its own. Visual Studio has the same core in C#.

## The host contract (what every IDE side does)

Taken from what the VS Code extension does today (`vscode/src/`); the IDs are
used by the tests and the gate.

| ID | Duty | VS Code today |
|---|---|---|
| H1 | Find the program: the user's own setting (user level only, never a project file), else the **bundled** one, else `cmcoder` on PATH, searched without the project folder. Never Python or `uv`. | `executable.ts`, `resolveExecutable` |
| H2 | Start it with `--protocol stdio` in the project folder, **no shell**, no console window, `NO_COLOR=1`; read stdout as UTF-8 JSON lines; stderr to the log | `agentProcess.ts` |
| H3 | Stop it: `shutdown`, then end stdin, then kill after 5 s; never leave it running when the project or IDE closes | `AgentProcess.stop` |
| H4 | Report a failed start or crash in the panel with **Restart**; "not found" says how to fix it | `describeSpawnError`, `setState` |
| H5 | Relay every event to the panel first, in order; the panel's messages to the program | `onAgentEvent`, `onWebviewMessage` |
| H6 | On `system_init`: mark ready, send `ide_capabilities` (`getDiagnostics`, `openFile`), send the editor context label; check `protocol_version` and say clearly if the program and plugin don't match | `case "system_init"` |
| H7 | Editor context with each message: active file, selection (1-based lines), errors/warnings (max 30); a toggle; a setting to turn it off | `editorContext.ts` |
| H8 | IDE tools: `getDiagnostics` (one file or all, max 200 lines), `openFile` (file, optional line) | `runIdeTool` |
| H9 | Permission requests with a change: open the IDE's **diff viewer** (setting to turn off); Accept / Reject there answer the request; closing clears it | `diffReview.ts` |
| H10 | Answer permission requests (allow, always, deny with feedback); tell the panel; close the diff | `answer` |
| H11 | `/rewind`: the IDE's own pickers (which message, what to undo, files outside the project) | `pickRewind` |
| H12 | History and resume (only a session id matching `^[A-Za-z0-9][A-Za-z0-9-]{0,63}$` becomes an argument), new conversation, continue last | `newConversation` |
| H13 | Attach a file (@): the IDE's file picker over the project | `attachFile` |
| H14 | "Ask cmcoder about selection" in the editor's context menu and a shortcut | `askAboutSelection` |
| H15 | Agent Navigator as an editor tab, live during the turn; Stop a subagent from it | `navigator.ts` |
| H16 | Code search: status in the status bar; set-up dialog; update the index | `codeSearch.ts` |
| H17 | Settings page: program path, extra arguments, default permission mode, editor context, diff review | `package.json` contributes |
| H18 | Open links only if `http(s)`, in the system browser; never navigate the panel | `openLink` |
| H19 | Panel security: no remote content, no inline scripts, a content security policy; the panel's files come from the plugin only | `html()` |
| H20 | Theme: the panel follows the IDE's light, dark and high-contrast themes, live | CSS `--vscode-*` |
| H21 | Project trust: a project's own `.cmcoder` settings only when the IDE trusts the project (JetBrains) or the user allows it (Visual Studio, Eclipse) — `--trust-project` | `isTrusted` |
| H22 | Show log; **Copy diagnostics** (new, every IDE): plugin and IDE versions, OS, program path and version, `cmcoder doctor --no-probe` (keys already masked), the last 300 log lines, to the clipboard. Nothing is sent anywhere | `showLog` |
| H23 | Telemetry label: the program is told which IDE runs it (`--client jetbrains`/`eclipse`/`visualstudio`/`vscode`), so usage counts per IDE when telemetry is on | `frontend = "vscode"` |
| H24 | Branding: the company's name and icon from `branding/` in the IDE's plugin list, tool window, panel and dialogs | `brand.ts`, `vsix.py` |

## The release gate (scenarios run in every IDE)

Each scenario runs in the real IDE, through its UI and embedded browser,
with the **installed** plugin file, the **bundled** program and the mock model
server (scripted replies, as the evals use), with Python removed from the
search path. A scenario passes in an IDE only if every check passes.

| # | Scenario | Checks |
|---|---|---|
| G1 | Open a project, open the panel | The panel loads (not blank), model name, mode, Critique box; `system_init` received; no errors in the browser console |
| G2 | Ask a question | The reply streams in as Markdown; raw HTML from the model is shown as text, never run |
| G3 | A turn that reads a file | Tool card with the file and a result summary |
| G4 | An edit that needs permission | The IDE's diff viewer opens with the change; **Accept** there → the file changes on disk and in the open editor |
| G5 | Deny with feedback | File unchanged; the model got the feedback; card shows "Denied" |
| G6 | A high-risk shell command | No "Always" button |
| G7 | Editor context | Open a file, select lines 3–5 → the message carries the path and lines; toggle off → nothing sent |
| G8 | IDE tools | `getDiagnostics` returns an error the IDE shows in a seeded broken file; `openFile` opens a file at line 10 |
| G9 | Two subagents in parallel | Agent map rows, then **Stop** on one → only that one stops; the Agent Navigator tab shows both branches and their steps (not blank) |
| G10 | Critique | Tick the box → saved in the user's settings; review lines appear; a second instance of the IDE follows within seconds |
| G11 | Slash commands | `/` shows the list; `/help`, `/cost`, `/todos` (todo list appears) |
| G12 | History | A second conversation lists the first; resume shows its messages |
| G13 | Rewind | The IDE's pickers; code goes back to before the message |
| G14 | Code search | Set up through the IDE's dialogs (mock embeddings); status shows the index; the model's `CodeSearch` call returns the seeded function |
| G15 | Interrupt | Stop / Esc during a reply → "interrupted"; a queued message comes back to the input |
| G16 | Program missing, program crashes | Clear message and **Restart**; Restart works |
| G17 | Awkward paths | Project under a path with spaces and non-ASCII letters (e.g. `Développement\Prj ä`), and on a second drive (Windows) |
| G18 | Themes | Light, dark and high-contrast: the panel's text and background follow the IDE (contrast checked on a screenshot) |
| G19 | Links | An `https` link in a reply opens in the system browser (intercepted in the test); `javascript:` and `file:` links do nothing |
| G20 | Closing | Closing the project and the IDE leaves no `cmcoder` process running; the conversation was saved |
| G21 | Untrusted project | A project's `.cmcoder/settings.json` is ignored, with the warning, until trusted |
| G22 | Copy diagnostics | Clipboard has versions and log lines, and never the API key |
| G23 | No Python | The whole gate runs with every Python, `uv` and `py` launcher off the search path and `PYTHON*` variables unset; on Windows also in a user profile path with a space |

Plus, outside the scenarios:

- **JetBrains compatibility**: the JetBrains **Plugin Verifier** against
  IntelliJ IDEA Community and Ultimate, Rider, PyCharm, WebStorm and GoLand
  from 2024.2 to the newest release: no use of missing or internal APIs. The
  gate's UI scenarios run in IntelliJ IDEA (oldest and newest supported) and
  a smoke subset (G1, G2, G4, G9) in Rider.
- **Eclipse**: the gate on the oldest (2024-06) and newest Eclipse.
- **Visual Studio**: the gate on the Visual Studio 2022 in GitHub's Windows
  image; the extension is compiled against the 17.10 SDK so it can't use
  anything newer than its declared minimum.
- **VS Code**: its existing tests plus G1–G23 for the per-platform `.vsix`,
  so all four IDEs pass the same list.

The gate report (`GATE-REPORT.md`) is a table of G1–G23 × IDE × OS with ✅ or
the failure. The release bundle is only assembled when every cell is ✅;
anything a test can't reach is listed under "Not covered by automated tests"
in `README-FIRST.txt`.

## Items, in order

### 1. No Python anywhere (the foundation)

- **Terminal package**: `terminal/cmcoder-<platform>.zip` (the standalone
  program folder) with `install.ps1` (Windows) and `install.sh`: copies to
  `%LOCALAPPDATA%\Programs\cmcoder` / `~/.local/share/cmcoder`, adds it to the
  **user's** PATH (no admin), and `uninstall` scripts. Tested on each OS in CI.
- **The "no Python" test environment**, used by every later gate job: a
  script that removes every Python, `uv` and `py` launcher directory from
  PATH and unsets `PYTHONHOME`, `PYTHONPATH` and friends, then runs the
  standalone tests and the gate. It first proves it works (`python` and
  `py` are not found).
- `cmcoder --client <name>` (H23).
- Git Bash: `doctor` and the Bash tool already name Git for Windows when bash
  is missing; a Windows CI test now runs the standalone program with Git
  Bash hidden and checks that it starts, answers, and gives that message for
  a shell command (so a developer without Git sees a fix, not a crash).
- VS Code: the per-platform `.vsix` becomes the only VS Code file in the
  bundle; its "not found" message stops suggesting `uv tool install`.
- **Done when**: the standalone tests and the VS Code `.vsix` pass in the
  no-Python environment on Windows, macOS and Linux.
- Status (6 Oct): built.
  - `packaging/no_python.py`: a "trap" folder first on PATH with `python`,
    `python3`, `py`, `pip`, `uv`, `uvx`, ... (shell scripts; on Windows real
    `.exe` files compiled with the C# compiler that comes with Windows, since
    Windows finds a bare `python` only as `python.exe`), Python's variables
    removed; anything that starts one is recorded and fails. Taking Python
    off PATH instead isn't possible everywhere (`/usr/bin` on Linux, the `py`
    launcher in `C:\Windows`). `tests/test_no_python.py` proves the trap
    works; `tests/test_standalone.py` runs in it with
    `CMCODER_TEST_NO_PYTHON=1` (the release workflow does).
  - Terminal package: `packaging/terminal.py` → `cmcoder-<platform>.zip`
    with `install.cmd`/`install.ps1` (Windows: `%LOCALAPPDATA%\Programs\cmcoder`,
    the user's PATH; the `.cmd` runs the script past PowerShell's execution
    policy for that one run), `install.sh` (`~/.local/share/cmcoder`, a link
    in `~/.local/bin`, `~/.profile`/`~/.zprofile` only if needed; macOS
    quarantine cleared), uninstall scripts that undo exactly that, and a
    README. `tests/test_terminal_package.py`: install, update, run from a new
    login shell, `doctor`, uninstall (the profile file back byte for byte);
    on Windows, under a path with a space and `ä`, in CI only (it changes the
    user's PATH, restored afterwards). `.gitattributes` pins CRLF for
    `.cmd`/`.ps1` and LF for `.sh`.
  - `--client vscode|jetbrains|eclipse|visualstudio` with `--protocol stdio`
    (telemetry counts sessions per IDE; the default stays `vscode`).
  - No Git Bash: a standalone test hides it; cmcoder answers and the shell
    command says to install Git for Windows.
  - VS Code's "not found" message points to the per-platform `.vsix`
    instead of `uv tool install`.
  - No hidden use of Python was found: as a standalone program, cmcoder
    starts itself (the sandbox bridge, the terminal profile), never Python.

### 2. One chat panel for every IDE

- Move the chat panel and the navigator to `clients/web-panel/` (TypeScript,
  built with esbuild into two `.js` files and two `.css` files). The panel
  talks only to a **bridge**: `post(message)`, `onMessage(handler)`, and the
  theme as CSS variables `--cm-*` (the VS Code bridge maps `--vscode-*` to
  them). Messages stay the ones in `webviewMessages.ts` (now `src/messages.ts`).
- A **test bridge** that the gate uses in every IDE: lets a test read what
  the panel shows and click its buttons through the embedded browser's
  JavaScript call (`executeJavaScript` in JCEF, `Browser.evaluate` in SWT,
  `ExecuteScriptAsync` in WebView2). It is compiled only into test builds.
- The browser tests (`webview.test.ts`, `navigator.test.ts`) move with the
  panel and run once against each bridge flavour.
- VS Code keeps working unchanged (its tests and the release build prove it).
- **Done when**: VS Code uses the shared panel, all its tests pass, and the
  panel runs in a plain browser with the test bridge.
- Status (6 Oct): built. `clients/web-panel/` (its own `package.json`; the
  VS Code extension's `npm ci` installs it, so building VS Code is unchanged):
  - `src/bridge.ts`: VS Code's API, WebView2's `chrome.webview`, or a function
    the IDE adds (`window.cmcoderHostPost`, JCEF and SWT); messages wait in
    the page until that function exists, so the first ("ready") isn't lost.
    To the page, every IDE runs `window.postMessage(...)`.
  - The icon comes from the IDE (`data-icon`), not a path in VS Code's folder.
  - `theme.json`: the 32 colours and fonts the pages use, with what each is for
    and dark/light defaults. **Changed from the plan**: they keep VS Code's
    variable names instead of new `--cm-*` names (VS Code sets them itself and
    its styles stay as they were); the other IDEs set the same names.
  - `src/test-driver.ts` (tests only): `__cmcoderTest.run(action, selector)`
    answering in JSON, for JCEF, SWT and WebView2 tests.
  - `README.md`: the contract for an IDE (page HTML, policy, messages, theme,
    security rules, the test driver).
  - Tests: the chat and navigator tests now run once per connection type
    (VS Code, WebView2, a late-added function): 54; plus the bridge's queue,
    the icon (an address that could break out of the CSS is refused), the test
    driver, and `theme.json` against the pages (and readable defaults): 59.
  - `src/protocol.ts` moved here; the existing check that it's regenerated
    when the protocol changes (`tests/test_protocol_ts.py`) follows it.
  - The real-VS Code test now also waits for the Agent Navigator's script to
    report "ready" (an empty tab would have passed before).
  - Old compiled tests are cleared before each test build (a moved test kept
    running from `dist-test/`).

### 3. Protocol for Java, Kotlin and C#

- The hosts only *read* a few events (`system_init`, `permission_request`,
  `ide_tool_request`, `result`, `rewind_points`) and *send* a few messages;
  everything else goes to the panel as raw JSON. So: small hand-written
  classes for those, plus a CI test that checks them against the schema from
  `cmcoder protocol-schema` (CI fails when the protocol changes and a host
  doesn't follow).
- `protocol_version` checked at start (H6).

### 4. The JVM core (shared by JetBrains and Eclipse)

- `clients/jvm-core/` (Java 17, no IDE classes, one small JSON library
  shaded in so it can't clash with the IDE's): program lookup (H1, the same
  rules and tests as `resolveExecutable`, including the planted-program case
  on Windows), process start/stop (H2–H4), JSON lines with UTF-8 across chunk
  boundaries, setting the executable bit on the bundled program after
  install (zip installers can drop it on macOS and Linux) and clearing
  macOS's quarantine flag.
- Tests with JUnit against the **real standalone program** and the mock model
  server: the same cases as `agentProcess.test.ts`.

### 5. JetBrains plugin

- `jetbrains/` (Kotlin, Gradle, IntelliJ Platform Gradle Plugin 2.x;
  `sinceBuild` 242, no upper bound unless the verifier finds a break).
- Tool window with the panel in **JCEF** (`JBCefBrowser`, messages through
  `JBCefJSQuery`; panel files served from the plugin, not `file://`;
  navigation blocked). If JCEF is unavailable (some remote-development or
  custom runtimes), the tool window says so and how to fix it.
- H1–H24 with the platform's APIs: `DiffManager` (H9), `FileEditorManager` /
  `SelectionModel` and the highlighting of the open file (H7, H8),
  `OpenFileDescriptor` (H8), `Configurable` + `PersistentStateComponent`
  (H17), a status bar widget (H16), actions and keymap (H14), trusted
  projects (H21), editor tabs for the navigator (H15), `LafManager` listener
  for themes (H20).
- One process per open project; closing the project stops it (H3).
- Packaging: one plugin `.zip` per platform with `bin/cmcoder/` inside.
- Tests: platform tests (headless) for the host duties, UI tests in a real
  IDE for the gate (IntelliJ's Starter/Driver test framework; Remote Robot if
  the former can't drive JCEF), Plugin Verifier. In GitHub CI on Windows,
  macOS and Linux.

### 6. Eclipse plugin

- `eclipse/` (Java 21, Maven + Tycho): a bundle (the view, handlers,
  preferences), a fragment per platform with `bin/cmcoder/` (so each archive
  only carries its own program; `p2.inf` sets the executable bit), a feature
  and a p2 repository archive per platform.
- A view with SWT's `Browser`, created **with the Edge engine on Windows**
  (`SWT.EDGE`; the old default engine can't run the panel), messages through
  `BrowserFunction` / `execute`; navigation blocked with a `LocationListener`.
- H1–H24 with Eclipse's APIs: Compare editor (`CompareUI`) for diffs (H9),
  `ITextEditor` selection and problem markers (`IMarker`) (H7, H8),
  `IDE.openEditor` + go to line (H8), a preference page (H17), a status-line
  contribution (H16), commands/handlers/key bindings (H14), an editor part for
  the navigator (H15), theme from the workbench's colours (H20). No trust
  concept in Eclipse: a preference "Use the project's own cmcoder settings",
  off by default (H21).
- Tests: Tycho Surefire with the UI harness and **SWTBot** for the gate, on
  Windows, macOS and Linux (Linux under a virtual display), on Eclipse
  2024-06 and the newest release.

### 7. Visual Studio 2022 extension

- `visualstudio/` (C#, .NET Framework 4.8 as Visual Studio requires, the
  VS SDK with `AsyncPackage`; `InstallationTarget` `[17.10,18.0)`, x64 only:
  the standalone program is built for x64).
- A tool window with the panel in **WebView2** (WPF control). Its data folder
  under `%LOCALAPPDATA%\cmcoder\webview2` (the default, next to `devenv.exe`,
  isn't writable); panel files mapped to a virtual host name (no `file://`);
  navigation, new windows, dev tools and the default context menu disabled in
  release builds; messages accepted only from the panel's own origin.
- The C# core: the same rules as the JVM core (H1–H4), with its own tests
  against the real standalone program.
- H5–H24 with Visual Studio's APIs: the difference service
  (`IVsDifferenceService`) for diffs (H9), the active document and selection
  and the **Error List** (H7, H8), `VsShellUtilities.OpenDocument` + go to
  line (H8), an options page (H17), the status bar (H16), commands in
  `.vsct` with the editor context menu and a shortcut (H14), a document
  window for the navigator (H15), `VSColorTheme` for themes (H20), the
  solution folder as the project (a solution's folder, or the open folder in
  "Open Folder" mode; a message if neither).
- Tests: unit tests for the core; the gate with Visual Studio's in-IDE test
  framework (`Microsoft.VisualStudio.Extensibility.Testing`) in GitHub's
  Windows runner, which has Visual Studio 2022 installed.

### 8. Release workflow and bundle

- `release.yml` grows to: build the standalone program per platform → build
  every IDE file around it → run each IDE's gate (the installed file, no
  Python) → assemble the bundle (`README-FIRST.txt`, `GATE-REPORT.md`,
  `SHA256SUMS.txt`) only if every gate job passed. On a version tag
  (`v0.6.0`, ...) the bundle is also attached to a GitHub release; otherwise
  it's a workflow artifact.
- One version number, from `pyproject.toml`, written into every plugin
  manifest at build time; a test checks they all match.
- Branding applied to every file (H24), checked at build time as today.

### 9. Security review

The Phase 4/5 review method, for what's new: each embedded browser's
security settings (H19: remote content, navigation, scripts from the model's
text, the bridge only reachable from the panel), the program lookup on each
platform (a program planted in the project), arguments built without a shell
(a session id can't become an option), files written by the plugins
(settings, WebView2 data), and secrets never in logs or in Copy diagnostics.
Findings fixed with a test that fails on the earlier code, as before.

### 10. Docs

- **For developers** (the people receiving the files), one page per IDE:
  install, first start (gateway and key, as today), the panel tour, settings,
  updating to a new file, uninstalling, troubleshooting (Git Bash missing,
  WebView2 missing, JCEF unavailable, antivirus or SmartScreen warnings until
  the files are code-signed, macOS "can't be opened"), and "Copy diagnostics"
  for reporting a problem.
- **For you / admins**: how to run the release workflow, read the gate
  report and hand out the bundle; managed settings still apply to every IDE.
- `ide-guide.md`: the plugin code explained, in the style of the Python
  guides (Kotlin, Java and C# sections); `python-guide.md` for the Python
  side (`--client`, the no-Python environment, the terminal package);
  `langgraph-guide.md` short (an IDE front end has no LangGraph counterpart;
  how LangGraph apps are usually wrapped instead).
- `STATUS.md` at the end, with the gate report of the final build.

## Risks and how the plan handles them

| Risk | Handling |
|---|---|
| A feature works in one IDE and not another | One shared panel; the host contract; the same 23 scenarios in every IDE; CI fails on a duty without a test |
| The installed file differs from what was tested | The gate installs the built file; a packaging test lists each file's contents |
| A hidden need for Python | The no-Python environment for every gate job (G23) |
| Old or new IDE versions break | Plugin Verifier (JetBrains), oldest and newest Eclipse, Visual Studio compiled against its minimum SDK |
| Embedded browser differences (JCEF, Edge in SWT, WebView2) | The panel uses plain DOM and no newer browser features than all three support; each gate runs in the real browser |
| The program loses its executable bit, or macOS quarantines it | Set on first start by the plugin (JVM core), tested on macOS and Linux CI |
| Unsigned programs warned about by SmartScreen, antivirus or Gatekeeper | Carried over from Phase 4 (needs a company certificate); the developer guide shows what to do; signing slots into the release workflow when a certificate exists |
| Slow iteration on JetBrains (built only in CI from here) | Most logic is in the shared panel and JVM core, built and tested here; the JetBrains layer is thin |
| Large files (a program per platform in each) | Per-platform files, so each is one program (60–100 MB) |

## Checklist

- [x] 1. No Python anywhere: terminal package, no-Python test environment, `--client`, Git Bash check
- [x] 2. One chat panel for every IDE (shared package, bridge, test bridge; VS Code on it)
- [ ] 3. Protocol classes for Java/Kotlin/C# checked against the schema
- [ ] 4. JVM core (program lookup, process, JSON lines, executable bit)
- [ ] 5. JetBrains plugin (H1–H24, gate in IntelliJ IDEA and Rider, Plugin Verifier)
- [ ] 6. Eclipse plugin (H1–H24, gate on 2024-06 and newest)
- [ ] 7. Visual Studio 2022 extension (H1–H24, gate in Visual Studio)
- [ ] 8. Release workflow, bundle, gate report
- [ ] 9. Security review
- [ ] 10. Docs for developers and admins; guides; STATUS
