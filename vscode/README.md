# cmcoder for VS Code

Chat with cmcoder in VS Code's side bar. The extension runs the same
`cmcoder` program as the command line (`cmcoder --protocol stdio`), with your
settings, API key, permission rules and sessions.

## Requirements

- `cmcoder` installed and working in a terminal (`cmcoder doctor` passes).
  The simplest: `uv tool install --editable <path to the cmcoder repository>`.
- On Windows, Git for Windows (for the Bash tool), as for the CLI.

## Install

```
code --install-extension cmcoder.vsix
```

`cmcoder.vsix` comes from CI (the `cmcoder-vsix` artifact) or from
`npm ci && npm run package` in this folder (Node.js 22+).

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
| `cmcoder.executable` | The `cmcoder` command, or its full path |
| `cmcoder.executableArgs` | Arguments before cmcoder's own, e.g. `["run", "--project", "C:\\path\\to\\cmcoder", "cmcoder"]` with `uv` as the executable |
| `cmcoder.permissionMode` | Permission mode for new conversations (user settings only) |
| `cmcoder.diffReview` | Open proposed edits in the diff editor (default on) |
| `cmcoder.autoContext` | Send the editor context with messages (default on) |

A workspace can't change which program runs or the permission mode: the
first two can't be set by an untrusted workspace, and the third only in your
user settings. The program is looked up on `PATH` only, never in the
workspace folder.

The project's own `.cmcoder` settings (`env`, allow rules, permissive modes)
are used only when VS Code trusts the workspace; gateway settings are never
read from a project. See "Project settings and trust" in the main README.

cmcoder's own settings (gateway, models, rules) stay in
`~/.cmcoder/settings.json`; **cmcoder: Open Settings File** opens it.
**cmcoder: Show Log** shows cmcoder's messages if something goes wrong.
