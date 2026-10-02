# cmcoder for VS Code

Chat with cmcoder in VS Code's side bar. The extension runs the same
`cmcoder` program as the command line (`cmcoder --protocol stdio`), with your
settings, API key, permission rules and sessions.

## Requirements

- `cmcoder` installed and working in a terminal (`cmcoder doctor` passes).
- On Windows, Git for Windows (for the Bash tool), as for the CLI.

## Use

1. Open your project folder in VS Code.
2. Click the cmcoder icon in the activity bar (or **Ctrl+Alt+K**).
3. Type a request and press **Enter** (**Shift+Enter** for a new line).

Permission prompts appear in the chat: **Allow**, **Always allow** (saved for
this project, like the CLI's "Always") or **Deny**, optionally telling
cmcoder what to do instead. **Esc** or **Stop** interrupts. The mode picker
at the top switches the permission mode.

## Settings

| Setting | Meaning |
|---|---|
| `cmcoder.executable` | The `cmcoder` command, or its full path |
| `cmcoder.executableArgs` | Arguments before cmcoder's own, e.g. to run it with `uv run` |
| `cmcoder.permissionMode` | Permission mode for new conversations |

cmcoder's own settings (gateway, models, rules) stay in
`~/.cmcoder/settings.json`; **cmcoder: Open Settings File** opens it.
**cmcoder: Show Log** shows cmcoder's messages if something goes wrong.
