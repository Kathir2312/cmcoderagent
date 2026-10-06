# Phase 6 security review: the IDE plugins

**Date:** 6 October 2026. **Scope:** what Phase 6 added. That is
- the shared chat page (`clients/web-panel`) as each IDE shows it: WebView2
  (Visual Studio), SWT's browser (Eclipse) and JavaFX's WebView (NetBeans);
- the IDE cores (`clients/jvm-core`, `visualstudio/src/Cmcoder.Core`): finding
  and starting cmcoder, the protocol;
- the plugins (`eclipse/`, `visualstudio/`, `netbeans/`);
- the terminal package, the release bundle, and what the plugins ship
  (OpenJFX, WebView2's SDK, Newtonsoft.Json).

The VS Code extension was reviewed in Phases 2–5; JetBrains was dropped.

**Result:** 2 issues fixed (1 medium, 1 low), both with a test or gate check
that fails on the earlier code. No finding open. One dependency kept under
watch (OpenJFX), now checked in CI.

## How it was tested

| Kind | Tool | What it covered |
|---|---|---|
| Gates | the release gates in the real IDEs | each plugin's page, bridge, navigation, diff review, diagnostics without the key, closing (Visual Studio, Eclipse, NetBeans) |
| SAST | Semgrep 1.179.0, `semgrep-rules` `a84ff9c` (java, csharp, typescript, javascript, bash, secrets) | 78 files: the plugins, the cores, the panel, the gate runners, the terminal package scripts |
| SAST (Python) | Bandit 1.9.4 | the new packaging scripts (CI already runs it on `src/`) |
| SCA | npm audit | the panel's and the VS Code extension's packages: 0 |
| SCA | `dotnet list package --vulnerable` | the Visual Studio core's NuGet packages: 0 |
| SCA | OSV (`packaging/osv_check.py`, new; in CI) | OpenJFX 21.0.12 and the Visual Studio extension's NuGet packages |
| Manual | review and tests | the bridges, what the page may load, program lookup, arguments, files written, secrets in logs and diagnostics |

## Findings

| # | Severity | Finding | Status |
|---|---|---|---|
| 1 | Medium | NetBeans: a script in the chat page could start a navigation; JavaFX then reported the other address, and messages were trusted by that address | Fixed |
| 2 | Low | Eclipse and NetBeans: a release build loaded the panel's test driver if a JVM setting named one | Fixed |

### 1. NetBeans: navigation and the page's messages

JavaFX's WebView can't refuse a navigation. The plugin cancelled one, but
after `location.href = "https://…"` the engine reported the other address
even though the chat page stayed. Messages were accepted when the engine's
address was the chat page, so they were then dropped; and had another page
loaded, its `alert()`s would have been read as messages.

The chat page shows only sanitized model text, and its CSP allows no remote
script, so a script had to come from the plugin's own files. Even so, the
bridge must not depend on the address.

**Fix:** a message must carry a secret made for that page load, which the
plugin gives only to its own page, after it loads. A navigation away is
cancelled as it starts; if another page ever loads, the chat page is loaded
again. No Java object is exposed to the page's script. A JavaFX `JSObject`
member would let a script call any of its public methods.

**Test:** the NetBeans gate's "the page stayed" step navigates away, then
checks that the page is the chat page and still answers. It failed on the
earlier code.

### 2. The test driver in release builds

The gates drive the real page through `test-driver.js`, which is loaded when
`-Dcmcoder.testDriver=<file>` is set. Eclipse and NetBeans also honoured that
setting in release builds. It is not a boundary: whoever can set it controls
the IDE's launch already. But a release shouldn't run a test hook. (Visual
Studio compiles it only into its test build.)

**Fix:**
- **Eclipse:** the setting counts only when the tests' fragment is installed.
- **NetBeans:** it counts only in the test build, the one with `Gate`. The
  gate's own start property is ignored by a release build too.

**Test:** both gates still pass with their test builds. A release build
ignores the setting, checked by reading the code. The release modules have
no test harness of their own.

## Checked, no change needed

- **What the model's text can do in the page:**
  - raw HTML is shown as text;
  - the rendered Markdown is filtered to a list of plain elements (no
    `img`, `iframe`, `style` or event attributes);
  - links keep only `http(s)`, `mailto` and `#`.

  Each page has a CSP: `default-src 'none'`, scripts only with the page's
  nonce, styles and images only from the plugin's files, no
  `connect-src` (Eclipse on Linux: only SWT's own `swt:` channel).
  Semgrep's `postMessage` origin warnings: no frame or window can reach the
  page. Frames are blocked by the CSP, pop-ups by every plugin, and the page
  has no opener.
- **Where messages may come from:**
  - Visual Studio: only from its page (`e.Source`). Dev tools are on only in
    the test build; host objects are off.
  - Eclipse: the function answers only on the chat page's URL.
  - NetBeans: finding 1.
- **Which program runs:**
  - the user's setting (a full path, user level only), then the plugin's
    copy, then `PATH` without the project folder;
  - `.bat`/`.cmd` on `PATH` are skipped.

  Tested in both cores: `aProgramPlantedInTheProjectIsNeverStarted`.
- **Arguments:** no shell anywhere. A session id from the page becomes an
  argument only if it matches `^[A-Za-z0-9][A-Za-z0-9-]{0,63}$`. Tested with
  `--permission-mode=bypassPermissions` as the id. Semgrep's
  "command injection" in `AgentProcess`, `Commands` and `Actions`: argument
  lists without a shell, with the located program.
- **Secrets:**
  - The Chroma API key from the IDEs' code search set-up goes to cmcoder,
    which puts it in the OS keychain.
  - The plugins log only cmcoder's stderr, its starts and its states, never
    a message they send.
  - cmcoder reports a rejected message by field and reason, never with its
    values. New test: `test_an_api_key_in_a_rejected_message_is_never_echoed`.
  - Copy Diagnostics is checked for the API key in every gate.
- **Files the plugins write:** the chat pages, per start, in the IDE's
  per-user state or cache folder; WebView2's data in
  `%LOCALAPPDATA%\cmcoder\webview2`. Nothing in the project.
- **Project settings:** used only when the user trusts the project:
  - Eclipse, NetBeans: off by default;
  - Visual Studio: its option;
  - VS Code: workspace trust.
- **The release bundle:** `SHA256SUMS.txt` for every file. No file is
  signed yet; that needs a company certificate, as in Phase 4.

## Under watch: OpenJFX's browser engine

The NetBeans plugin ships OpenJFX 21 (LTS), whose WebView is a WebKit.
Browser engines get security fixes often. The plugin shows only its own page
with sanitized text, which narrows what reaches the engine, but it should
stay current:
- `packaging/osv_check.py` runs in CI and fails on any known advisory for the
  shipped version.
- Raise `javafx.version` in `netbeans/pom.xml` to the newest 21.0.x with each
  release.

JavaFX 21 needs the JDK's `jdk.jsobject` module (still in JDK 25). If a later
JDK drops it, the plugin moves to a JavaFX that carries its own copy.
