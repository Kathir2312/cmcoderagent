# cmcoder Phase 6, explained for Java and C# developers

How the IDE plugins are built: one chat page shown in each IDE's browser, one
"host" that talks to cmcoder, and a thin layer per IDE. Read with the
[plan](PLAN.md) (its host duties H1–H24 are named below) and the
[security review](SECURITY-REVIEW.md).

---

## 1. The shape

```
 chat page (TypeScript, clients/web-panel)   ← the same files in every IDE
        │  bridge: postMessage / cmcoderHostPost / WebView2 messages
 IDE layer (Eclipse, NetBeans: Java · Visual Studio: C#)
        │  calls into
 Host (clients/jvm-core · visualstudio/src/Cmcoder.Core)   ← no IDE classes
        │  JSON lines over stdin/stdout
 cmcoder --protocol stdio --client <ide>   (the standalone program, bundled)
```

cmcoder does all the work: the agent, tools, permissions, sessions, code
search. The plugins show, relay, and do what only the IDE can do: the editor
context, the diff viewer, IDE tools. So a fix in cmcoder or the page reaches
every IDE.

## 2. The chat page and its bridge

*Code:* `clients/web-panel/src` (`bridge.ts`, `chat/main.ts`,
`navigator/main.ts`, `test-driver.ts`). *Tests:* `clients/web-panel/test`
(Node's test runner with Playwright's Chromium).

The page never knows which IDE it's in. `bridge()` picks the way to talk to
the host:

| IDE | page → IDE | IDE → page |
|---|---|---|
| VS Code | `acquireVsCodeApi().postMessage` | VS Code's `postMessage` |
| Visual Studio | `window.chrome.webview.postMessage` (WebView2) | `PostWebMessageAsJson` |
| Eclipse | `window.cmcoderHostPost(json)`: an SWT `BrowserFunction` | `browser.execute("window.postMessage(JSON.parse(…))")` |
| NetBeans | `window.cmcoderHostPost(json)`: defined by the plugin as an `alert()` with a per-load secret | `engine.executeScript(…)` (JavaFX) |

The function may appear after the page's script runs, so the bridge queues
messages until it exists (`bridge.test.ts`). `data-host="function"` on
`<body>` forces the function path. Eclipse needs it because SWT's Edge also
has `chrome.webview`, which belongs to SWT.

The page's CSP allows only the plugin's own files and scripts with the page's
nonce. Model text is Markdown, filtered to plain elements (H19).

## 3. The host

*Code:* `Host.java` / `Host.cs`, with `AgentProcess`, `ProgramLocator`,
`Protocol`, `EditorContext`, `CodeSearch`, `Json`. *Tests:* `HostTest`,
`AgentProcessTest`, `ProgramLocatorTest`, `CodeSearchTest` (and the C#
`*Tests.cs`), against the real cmcoder and the mock model server.

- **ProgramLocator (H1):** the user's setting (a full path), then the
  plugin's own copy (`bin/cmcoder/`), then `PATH` without the project folder
  (Windows searches the current folder first, which would run a planted
  `cmcoder.exe`). `prepare` restores the executable bit, which zip-based
  installs (`.nbm`, Eclipse sites) lose.
- **AgentProcess (H2, H3):**
  - an argument list, no shell; `NO_COLOR=1`;
  - stdout read as UTF-8 JSON lines, stderr to the log;
  - stopping: `shutdown`, then close stdin, then kill after 5 s.
  - On Windows the C# core puts cmcoder in a **job object** that ends with
    Visual Studio, even if Visual Studio crashes.
  - A listener's exception is logged, never thrown on the reader thread
    (it would end the IDE).
- **Host (H4–H16):** a state machine (`starting`, `ready`, `exited`, …) and a
  switch over the page's messages (`ready`, `send`, `resume`, `answer`,
  `attachFile`, `openLink`, …) and cmcoder's events. Rules worth knowing:
  - a session id becomes `--resume=<id>` only if it matches
    `^[A-Za-z0-9][A-Za-z0-9-]{0,63}$`;
  - links reach the IDE only if they are `http(s)`.
- **Threads, the lesson of the Visual Studio gate.** Host methods are
  `synchronized`, and Host may wait for the UI thread (the editor context).
  So the UI thread must never wait for Host's lock. Every plugin calls Host
  from **one background thread** (`Session.run`). `state()`, `running()` and
  `send()` are lock-free (volatile fields), because a UI thread that read
  them under the lock deadlocked Visual Studio.

## 4. The IDE layers

Each plugin has the same few classes. The IDE APIs differ:

| Duty | Eclipse | Visual Studio | NetBeans |
|---|---|---|---|
| Browser | SWT `Browser` (Edge on Windows, WebKitGTK, WebKit) | WebView2 with virtual host names | OpenJFX `WebView` in a `JFXPanel` (bundled) |
| Chat window | `ViewPart` | `ToolWindowPane` | `TopComponent` (right side) |
| Diff with Accept/Reject (H9, H10) | `CompareEditorInput` + buttons | diff window + InfoBar | `DiffController` in an editor tab |
| Editor context (H7) | `ITextEditor`, problem markers | DTE `ActiveDocument`, Error List | `EditorRegistry`, `ErrorProvider`s |
| IDE tools (H8) | markers, `IDE.openEditor` | Error List, `VsShellUtilities` | `ErrorProvider`s, `LineCookie` |
| Settings (H17, H21) | preference page | `DialogPage` | `OptionsPanelController` |
| Log, diagnostics (H22) | console, Copy Diagnostics | Output pane | Output window |
| Packaging | p2 site: plugin + a fragment per platform | `.vsix` | `.nbm` per platform (+ OpenJFX) |

Look at `Session` first in each: it holds Host, implements `Ide` (what Host
asks of the IDE), and moves work between the UI thread and the session
thread.

NetBeans has two details of its own (`netbeans/README.md`):
- JavaFX can't refuse a navigation, so the plugin cancels one and checks the
  document.
- Messages are trusted by a secret per page load, not by the engine's
  address.

## 5. The gates

A gate is the plugin's real file in the real IDE, driven through the real
page by `test-driver.js`, with cmcoder from the file, the mock model server
(`cmcoder.testing.mock_server`, scripted replies) and no Python on `PATH`
(`packaging/no_python.py`):

| IDE | Self-test | Runner |
|---|---|---|
| Eclipse | `GateTest.java` (JUnit in Tycho's UI harness) | `mvn verify` |
| Visual Studio | `Gate.cs` (`#if CMCODER_GATE`) in the experimental instance | `gate/run-gate.ps1` |
| NetBeans | `src/gate/Gate.java` (the `-Pgate` build) in a fresh user folder | `gate/run-gate.sh` |

Each writes its steps to a file as it goes, so a hang still shows how far it
got. The gates found most of the bugs in this phase, for example:
- the WebView2 `Deny` mapping blocked the page's own scripts;
- the UI-thread deadlock;
- JavaFX's navigation and address.
