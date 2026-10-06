# cmcoder in NetBeans

The NetBeans plugin is one `.nbm` file per platform with cmcoder inside, and
the browser its chat needs. It needs **no Python** and no terminal package.

**Apache NetBeans 28 or newer** (28 to 31 are tested), running on JDK 17 or
newer (21 or 25 recommended). On Linux, the chat needs GTK 3 (`libgtk-3-0`),
which desktop Linux has.

## Install

1. Take the file for your platform: `cmcoder-netbeans-win32-x64.nbm`
   (Windows), `cmcoder-netbeans-darwin-arm64.nbm` (Apple silicon Mac) or
   `cmcoder-netbeans-linux-x64.nbm`.
2. **Tools → Plugins → Downloaded → Add Plugins…**, choose the file, then
   **Install**. Accept the license, then **Continue** past the warning that it
   isn't signed (until your company signs it).
3. Restart NetBeans when asked.
4. First time only: [setup.md](setup.md). For the API key without the
   terminal package, ask your team, or run the cmcoder inside the plugin once
   with `login`: **Tools → cmcoder → Copy cmcoder Diagnostics** shows where it
   is (the `Program:` line, ending in `cmcoder-netbeans/bin/cmcoder/cmcoder`).

**Update:** install the new `.nbm` the same way. **Uninstall:** **Tools →
Plugins → Installed →** cmcoder **→ Uninstall** (your settings and conversations
stay in `~/.cmcoder`).

## Use

1. Open your project (or just a file in a git repository).
2. **Ctrl+Alt+J** (Mac: Cmd+Alt+J), or **Tools → cmcoder → Open cmcoder Chat**.
   The chat opens on the right.
3. Type a request and press **Enter** (**Shift+Enter** for a new line).

| What | How |
|---|---|
| Permission prompts | in the chat: **Allow**, **Always allow** (this project), **Deny** with a note |
| Review a change | it opens in a diff tab with **Accept**, **Accept Always**, **Reject** above it |
| What cmcoder sees | the open file, the selection and its problems go with each message (the 📎 line) |
| Ask about code | select it, then **Ctrl+Alt+K** (Mac: Cmd+Alt+K) or right-click → **Ask cmcoder About Selection** |
| Earlier conversations | **History** at the top of the chat |
| Stop | **Esc** or **Stop** |
| Background helpers | **Tools → cmcoder → Open cmcoder Agent Navigator** (an editor tab) |
| Code search | the **Code search** button above the chat: set up, update, search ([code-search.md](code-search.md)) |
| Log, diagnostics | **Tools → cmcoder → Show cmcoder Log** (Output window) or **Copy cmcoder Diagnostics** |

cmcoder works on the project of the file you're editing, else the main
project, else the only open project; for a file outside any project, its git
repository. Closing the chat stops cmcoder; opening it starts a new one (your
conversations are kept: **History**).

## Options (Tools → Options → Miscellaneous → cmcoder)

| Option | Meaning |
|---|---|
| cmcoder program | **leave empty**: the copy inside the plugin is used |
| Permission mode at start | ask / accept edits / plan (read only) |
| Send the open file, the selection and its problems | default on |
| Show proposed changes in the diff viewer | default on |
| Use the project's own .cmcoder settings | off by default; switch on only for projects you trust |

cmcoder's own settings (gateway, model, rules) are in
`~/.cmcoder/settings.json` ([setup.md](setup.md)).

Something wrong: [troubleshooting.md](troubleshooting.md).
