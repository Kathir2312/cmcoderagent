# cmcoder for VS Code

Chat with cmcoder in VS Code's side bar. The extension runs the same
`cmcoder` program as the command line (`cmcoder --protocol stdio`), with your
settings, API key, permission rules and sessions.

User guide: [docs/guides/vscode.md](../docs/guides/vscode.md).

## Requirements

- Nothing else: the release `.vsix` (`cmcoder-<platform>.vsix`, from the
  Release build) contains the standalone cmcoder and uses it, so the PC needs
  no Python, uv or Node.js (docs/DESIGN.md D16).
- On Windows, Git for Windows (for the Bash tool), as for the CLI.

## Install

```
code --install-extension cmcoder-win32-x64.vsix
```

For maintainers: `uv run python packaging/build.py` then
`uv run python packaging/vsix.py` builds `cmcoder-<platform>.vsix` with the
program inside. `npm ci && npm run package` here (Node.js 22+) builds a
development `.vsix` without it, which needs `cmcoder` on PATH.

## Use

1. Open your project folder in VS Code.
2. Click the cmcoder icon in the activity bar (or **Ctrl+Alt+K**).
3. Type a request and press **Enter** (**Shift+Enter** for a new line).

| Feature | How |
|---|---|
| Permission prompts | In the chat: **Allow**, **Always allow** (saved for this project, like the CLI's "Always"), **Deny** with an optional message for cmcoder |
| Review edits | A proposed Write/Edit opens in the diff editor; **✓ Accept** / **✕ Reject** in its title bar (or on the chat card) |
| Editor context | The active file, selection and its problems go with each message; the 📎 line shows what, and its checkbox leaves it out once |
| Ask about code | Select code, then **Ctrl+Alt+L** or right-click → **Ask cmcoder About Selection** |
| Mention a file | The **@** button picks a workspace file |
| IDE tools | cmcoder can read the Problems panel (`getDiagnostics`) and open files for you (`openFile`) |
| Past conversations | **History** at the top; pick one to resume it. **Continue Last Conversation** in the view's `…` menu |
| Stop | **Esc** or **Stop** |
| Permission mode | The picker at the top (same modes as Shift+Tab in the CLI) |
| CLI in VS Code | **cmcoder: Open in Terminal** (terminal icon in the chat's title bar) |

## Settings

| Setting | Meaning |
|---|---|
| `cmcoder.executable` | Leave unset: the bundled cmcoder is used. For development only: another `cmcoder`, or its full path |
| `cmcoder.executableArgs` | Development only: arguments before cmcoder's own, e.g. `["run", "--project", "C:\\path\\to\\cmcoder", "cmcoder"]` with `uv` as the executable (needs Python) |
| `cmcoder.permissionMode` | Permission mode for new conversations (user settings only) |
| `cmcoder.diffReview` | Open proposed edits in the diff editor (default on) |
| `cmcoder.autoContext` | Send the editor context with messages (default on) |

A workspace can't change which program runs or the permission mode: the
first two can't be set by an untrusted workspace, and the third only in your
user settings. Without the bundled program, `cmcoder` is looked up on `PATH`
only, never in the workspace folder.

The project's own `.cmcoder` settings (`env`, allow rules, permissive modes)
are used only when VS Code trusts the workspace; gateway settings are never
read from a project. See "Project settings and trust" in the main README.

cmcoder's own settings (gateway, models, rules) stay in
`~/.cmcoder/settings.json`; **cmcoder: Open Settings File** opens it.
**cmcoder: Show Log** shows cmcoder's messages if something goes wrong.
