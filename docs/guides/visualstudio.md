# cmcoder in Visual Studio

The Visual Studio extension is one `.vsix` file with cmcoder inside. It needs
**no Python** and no terminal package.

**Visual Studio 2022 version 17.10 or newer** (Community, Professional,
Enterprise; 64-bit) on Windows x64. The chat uses the Microsoft Edge WebView2
Runtime, which Visual Studio installs.

## Install

1. Close Visual Studio.
2. Double-click `cmcoder-visualstudio-win32-x64.vsix`, then **Install**.
   Confirm if it says the file isn't signed (until your company signs it).
3. Start Visual Studio and open a solution or a folder (**File → Open →
   Folder**).
4. First time only: [setup.md](setup.md). For the API key without the
   terminal package, ask your team, or run the cmcoder inside the extension
   once with `login`: **Tools → cmcoder → Copy Diagnostics** shows where it
   is (the `Program:` line, ending in `bin\cmcoder\cmcoder.exe`).

**Update:** install the new `.vsix` the same way. **Uninstall:**
**Extensions → Manage Extensions → Installed →** cmcoder **→ Uninstall**
(your settings and conversations stay in `%USERPROFILE%\.cmcoder`).

## Use

1. Open a solution or a folder; cmcoder works in its folder.
2. **Tools → cmcoder → Open Chat**, or **Ctrl+Shift+Alt+C**. The chat docks
   next to Solution Explorer.
3. Type a request and press **Enter** (**Shift+Enter** for a new line).

| What | How |
|---|---|
| Permission prompts | in the chat: **Allow**, **Always allow** (this solution), **Deny** with a note |
| Review a change | it opens in the diff window, with **Accept**, **Accept Always**, **Reject** above it |
| What cmcoder sees | the open file, the selection and its Error List entries go with each message (the 📎 line) |
| Ask about code | select it, then **Ctrl+Shift+Alt+K** or right-click → **Ask cmcoder About Selection** |
| Earlier conversations | **History** at the top of the chat |
| Stop | **Esc** or **Stop** |
| Background helpers | the chat's navigator button, or **Tools → cmcoder → Open Agent Navigator** |
| Code search | the button above the chat, or **Tools → cmcoder → Code Search…** ([code-search.md](code-search.md)) |
| Log, diagnostics | **Tools → cmcoder → Show Log** (Output window) or **Copy Diagnostics** |

Closing the solution stops cmcoder; so does closing Visual Studio, even if it
crashes.

## Options (Tools → Options → cmcoder)

| Option | Meaning |
|---|---|
| cmcoder program | **leave empty**: the copy inside the extension is used |
| Permission mode at start | ask / accept edits / plan (read only) |
| Send the editor context | default on |
| Review changes in the diff window | default on |
| Use the solution's own .cmcoder settings | off by default; switch on only for solutions you trust |

cmcoder's own settings (gateway, model, rules) are in
`%USERPROFILE%\.cmcoder\settings.json` ([setup.md](setup.md)).

Something wrong: [troubleshooting.md](troubleshooting.md).
