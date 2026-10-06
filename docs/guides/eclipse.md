# cmcoder in Eclipse

The Eclipse plugin is one `.zip` (an update site) for Windows, macOS and
Linux, with cmcoder inside. It needs **no Python** and no terminal package.

**Eclipse 2024-06 or newer** (any package: Java, C/C++, Enterprise…), which
runs on Java 21. On Windows the chat uses the Microsoft Edge WebView2
Runtime (part of Windows 11 and Edge); on Linux, WebKitGTK
(`libwebkit2gtk-4.1-0`).

## Install

1. **Help → Install New Software… → Add… → Archive…** and choose the
   `cmcoder-eclipse` zip (don't unzip it). Name: `cmcoder`. **Add**.
2. Tick **cmcoder**, **Next**, accept the license, **Finish**.
3. Eclipse warns that the content is unsigned (until your company signs it):
   **Install Anyway**. Restart Eclipse.
4. First time only: [setup.md](setup.md). For the API key without the
   terminal package, ask your team, or run the `cmcoder` inside the plugin
   once: `cmcoder login` (it's in Eclipse's `plugins` folder, in
   `cmcoder.eclipse.<platform>_<version>/bin/cmcoder/`, for example
   `cmcoder.eclipse.win32.x86_64_…\bin\cmcoder\cmcoder.exe login`).

**Update:** the same steps with the new zip (or **Help → Check for Updates**
if the site was added from a shared folder or web server). **Uninstall:**
**Help → About Eclipse IDE → Installation Details →** cmcoder **→ Uninstall**.

## Use

1. Select a project in the Project Explorer (cmcoder works in it).
2. **Ctrl+Alt+J** (Mac: Cmd+Alt+J), or **Window → Show View → Other… →
   cmcoder → cmcoder**.
3. Type a request and press **Enter** (**Shift+Enter** for a new line).

| What | How |
|---|---|
| Permission prompts | in the chat: **Allow**, **Always allow** (this project), **Deny** with a note |
| Review a change | it opens in the compare editor with **Accept**, **Accept Always**, **Reject** |
| What cmcoder sees | the open editor, the selection and its problems go with each message (the 📎 line) |
| Ask about code | select it, then **Ctrl+Alt+K** (Mac: Cmd+Alt+K) or right-click → **Ask cmcoder About Selection** |
| Earlier conversations | **History** at the top of the chat |
| Stop | **Esc** or **Stop** |
| Background helpers | the chat's navigator button (**Agent Navigator** view) |
| Code search | the item in the chat view's toolbar: set up, update, search ([code-search.md](code-search.md)) |
| Log, diagnostics | **Ctrl+3**, then **cmcoder: Show Log** (Console) or **cmcoder: Copy Diagnostics** |

Closing the chat view stops cmcoder; opening it starts a new one (your
conversations are kept: **History**).

## Preferences (Window → Preferences → cmcoder)

| Preference | Meaning |
|---|---|
| cmcoder program | **leave empty**: the copy inside the plugin is used |
| Permission mode at start | ask / accept edits / plan (read only) |
| Send the open file, the selection and its problems | default on |
| Show proposed changes in the compare editor | default on |
| Use the project's own .cmcoder settings | off by default; switch on only for projects you trust |

cmcoder's own settings (gateway, model, rules) are in
`~/.cmcoder/settings.json` ([setup.md](setup.md)).

Something wrong: [troubleshooting.md](troubleshooting.md).
