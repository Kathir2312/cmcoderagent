# cmcoder in VS Code

The VS Code extension is one `.vsix` file with cmcoder inside it. It needs
**no Python, uv or Node.js**, and no terminal package.

## Install

1. Take the file for your platform: `cmcoder-win32-x64.vsix` (Windows),
   `cmcoder-darwin-arm64.vsix` (Apple silicon Mac) or `cmcoder-linux-x64.vsix`.
2. In VS Code: **Extensions** (Ctrl+Shift+X) → **⋯** (top right) →
   **Install from VSIX…** → choose the file.
   Or in a terminal: `code --install-extension cmcoder-win32-x64.vsix`.
3. Reload VS Code if it asks.
4. First time only: [setup.md](setup.md) (it shows where the extension's
   own cmcoder is, for `cmcoder login`).

**Update:** install the new `.vsix` the same way. **Uninstall:** Extensions →
cmcoder → **Uninstall** (your settings and conversations stay in `~/.cmcoder`).

## Use

1. Open your project folder (**File → Open Folder**).
2. Click the cmcoder icon in the activity bar, or press **Ctrl+Alt+K**
   (Mac: **Cmd+Alt+K**).
3. Type a request and press **Enter** (**Shift+Enter** for a new line).

| What | How |
|---|---|
| Permission prompts | in the chat: **Allow**, **Always allow** (this project, from now on), **Deny** with a note for cmcoder |
| Review a change | it opens in the diff editor; **✓ Accept** / **✕ Reject** in its title bar (or on the chat card) |
| What cmcoder sees | the open file, the selection and its problems go with each message; the 📎 line shows what; untick it to leave it out once |
| Ask about code | select it, then **Ctrl+Alt+L** (Mac: Cmd+Alt+L) or right-click → **Ask cmcoder About Selection** |
| Mention a file | the **@** button |
| Earlier conversations | **History** at the top; **Continue Last Conversation** in the view's **⋯** menu |
| Stop | **Esc** or **Stop** |
| Permission mode | the picker at the top: ask / accept edits / plan (read only) |
| Background helpers | **cmcoder: Open Agent Navigator** |
| Code search | the status bar item, or **cmcoder: Set Up Code Search** / **Update Code Index** / **Code Search…** ([code-search.md](code-search.md)) |
| The terminal version | **cmcoder: Open in Terminal** (terminal icon in the chat's title bar) |
| Something wrong | **cmcoder: Show Log**; [troubleshooting.md](troubleshooting.md) |

All commands start with **cmcoder:** in the Command Palette (Ctrl+Shift+P).

## Settings (File → Preferences → Settings → search "cmcoder")

| Setting | Meaning |
|---|---|
| `cmcoder.permissionMode` | the permission mode for new conversations (your user settings only) |
| `cmcoder.diffReview` | show proposed changes in the diff editor (default on) |
| `cmcoder.autoContext` | send the open file, selection and problems (default on) |
| `cmcoder.executable`, `cmcoder.executableArgs` | **leave these alone.** Unset, the extension uses the cmcoder inside it. They exist only for cmcoder's own developers (running it from source with `uv`), which needs Python |

cmcoder's own settings (gateway, model, rules) are in
`~/.cmcoder/settings.json`: **cmcoder: Open Settings File**.

A project's own `.cmcoder` settings are used only when VS Code trusts the
workspace (**Manage Workspace Trust**); a project can never change the gateway
or which program runs.
