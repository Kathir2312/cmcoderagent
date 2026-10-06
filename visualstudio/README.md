# cmcoder for Visual Studio

The chat panel, the Agent Navigator and proposed changes in Visual Studio's
diff window, for Visual Studio 2022 (17.10 and newer, 64-bit). cmcoder itself
is inside the extension, so developers install one file and need nothing
else: no Python (docs/DESIGN.md D16).

## For developers: install

1. Close Visual Studio, then double-click `cmcoder-visualstudio.vsix` and
   choose **Install** (it is unsigned until the company signs it: confirm).
2. Start Visual Studio and open a solution or a folder.
3. **Tools → cmcoder → Open Chat**, or **Ctrl+Shift+Alt+C**.

The panel needs the Microsoft Edge WebView2 Runtime (part of Windows 11 and
of Microsoft Edge); without it the chat says what to install.

| What | Where |
|---|---|
| The chat | **Tools → cmcoder → Open Chat** (docks next to Solution Explorer) |
| Ask about the selection | the editor's context menu, or **Ctrl+Shift+Alt+K** |
| Agent Navigator | the chat's button, or **Tools → cmcoder → Open Agent Navigator** |
| Proposed changes | the diff window, with **Accept**, **Accept Always**, **Reject** above it |
| Code search | the button above the chat, or **Tools → cmcoder → Code Search…** |
| Settings | **Tools → Options → cmcoder** |
| Log, Copy Diagnostics | **Tools → cmcoder → Show Log** (Output window) / **Copy Diagnostics** |

cmcoder works on the open solution's folder (or the folder opened with
**File → Open → Folder**). Closing the solution stops it; so does closing
Visual Studio (also if it crashes: cmcoder runs in a job object that ends
with Visual Studio).

## For maintainers: build and test

| Folder | What |
|---|---|
| `src/Cmcoder.Core` | the IDE side without Visual Studio (netstandard2.0; the JVM core's rules, in C#) |
| `test/Cmcoder.Core.Tests` | its tests against the real cmcoder and the mock model server (any OS) |
| `src/Cmcoder.VisualStudio` | the extension (VSIX, .NET Framework 4.8) |
| `gate/run-gate.ps1` | the release gate in a real Visual Studio |

```
# the core, on any OS (CMCODER_TEST_PYTHON: a Python with cmcoder, for the mock
# server; on Windows also CMCODER_TEST_BINARY: the standalone cmcoder.exe)
dotnet test visualstudio/test/Cmcoder.Core.Tests

# the extension, on Windows with Visual Studio 2022 and its extension workload
(cd clients/web-panel && npm ci && npm run build)
pwsh visualstudio/build-vsix.ps1          # bin\Release\Cmcoder.VisualStudio.vsix
pwsh visualstudio/build-vsix.ps1 -Gate    # the test build (never shipped)
```

The release workflow copies the standalone cmcoder into
`src/Cmcoder.VisualStudio/bin/cmcoder/` first, builds both, and runs
`gate/run-gate.ps1`: it installs the gate build into Visual Studio's
experimental instance, starts the mock model server, opens a small folder,
and the extension's self-test (`Gate.cs`) runs a whole conversation through
the real chat page: the bundled cmcoder starts, the editor context, the
Error List as an IDE tool, a change accepted and one rejected in the diff
window, the navigator, blocked navigation, Ask About Selection, diagnostics
without the API key, and closing the folder stopping cmcoder. No Python is
on PATH for any of it.
