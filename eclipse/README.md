# cmcoder for Eclipse

The chat panel, the Agent Navigator and proposed changes in Eclipse's compare
editor, for Eclipse 2024-06 and newer (Java 21 runs Eclipse; the plugin
itself needs 17). cmcoder itself is inside the plugin, so developers install
one file and need nothing else: no Python (docs/DESIGN.md D16).

## For developers: install

1. **Help → Install New Software… → Add… → Archive…** and pick
   `cmcoder-eclipse-<version>.zip` (one file for Windows, macOS and Linux:
   Eclipse installs only your platform's cmcoder).
2. Tick **cmcoder**, **Next**, accept, **Finish**; trust the unsigned content
   when asked; restart Eclipse.
3. **Window → Show View → Other… → cmcoder**, or **Ctrl+Alt+J**
   (**⌘⌥J** on macOS).

On Windows the panel needs the Microsoft Edge WebView2 Runtime (part of
Windows 11 and of Microsoft Edge). On Linux it needs WebKitGTK
(`libwebkit2gtk-4.1-0`). Without them the view says what to install.

| What | Where |
|---|---|
| The chat | the **cmcoder** view |
| Ask about the selection | editor context menu, or **Ctrl+Alt+K** |
| Agent Navigator | the chat's button, or the **cmcoder: Open Agent Navigator** command |
| Settings | **Window → Preferences → cmcoder** |
| Log, Copy Diagnostics | the **cmcoder: Show Log** / **Copy Diagnostics** commands (Ctrl+3) |

cmcoder works on the project of the open editor (else the selected one, else
the only open project). Closing the chat view stops it.

## For maintainers: build and test

```
(cd ../clients/web-panel && npm ci && npm run build)   # the shared panel first
mvn -B verify                                          # oldest Eclipse (2024-06)
mvn -B -Dtarget=newest verify                          # newest Eclipse
```

`site/target/cmcoder-eclipse-<version>.zip` is the update site archive. The
release workflow puts each platform's standalone cmcoder into
`fragments/cmcoder.eclipse.<platform>/bin/cmcoder/` first; without it the
plugin looks for `cmcoder` on PATH.

The gate test (`tests/`) runs a real Eclipse with the real chat page, a real
cmcoder and the mock model server: it needs `CMCODER_TEST_PYTHON` (a Python
with cmcoder, for the mock server), `xvfb-run -a` on Linux, and on Windows
`CMCODER_TEST_BINARY` (the standalone `cmcoder.exe`). With
`CMCODER_TEST_BUNDLED=1` it uses the cmcoder in the fragment instead, as
developers get it (the release build does this on every platform, with no
Python on PATH).

| Folder | What |
|---|---|
| `plugins/cmcoder.eclipse` | the plugin; also compiles `clients/jvm-core` (the host logic shared with JetBrains) |
| `fragments/` | one per platform, with that platform's cmcoder |
| `features/`, `site/` | what developers install, and the update site archive |
| `tests/` | the gate |
| `target-platform/` | the oldest and newest Eclipse built and tested against |
