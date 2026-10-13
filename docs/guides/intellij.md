# cmcoder for IntelliJ IDEA and other JetBrains IDEs: install and use

The plugin is **one `.zip` file per platform**, with cmcoder inside. The same
file works in **IntelliJ IDEA** (Community or Ultimate), **PyCharm**,
**Rider**, **WebStorm**, **GoLand**, **PhpStorm**, **CLion**, **RubyMine** and
**Android Studio**. You don't need Python or the terminal package.

![The cmcoder chat in IntelliJ IDEA](screenshots/intellij-1-chat.png)

## What you need

| | |
|---|---|
| IDE | a JetBrains IDE **2024.2 or newer** (2024.2 and the newest IntelliJ IDEA are tested on every platform; the newest PyCharm too) |
| Windows | **Git for Windows** (cmcoder runs commands with Git Bash) |
| Network | access to your company's AI gateway (often: the VPN) |
| From your AI team | the gateway address, the model name, your API key |

The chat uses the browser built into the IDE (JCEF): nothing else to install.

## 1. Get the file

Take the file for your computer. Your team shares them, or on GitHub go to
**Actions → Release build →** the latest green run **→ Artifacts**. GitHub
wraps each download in a `.zip` of its own: unzip that once to get the file
below, and **keep that one zipped**.

| Your computer | File |
|---|---|
| Windows | `cmcoder-jetbrains-win32-x64.zip` |
| Mac (Apple silicon) | `cmcoder-jetbrains-darwin-arm64.zip` |
| Linux | `cmcoder-jetbrains-linux-x64.zip` |

The file must match your computer: the cmcoder inside another platform's
file can't run on yours.

## 2. Install

1. **File → Settings → Plugins** (Mac: **IntelliJ IDEA → Settings… →
   Plugins**; in PyCharm, Rider and the others, the same with their name).
2. The **⚙** at the top → **Install Plugin from Disk…**, then choose the
   `.zip` (don't unzip it).
3. If the IDE shows its note about third-party plugins: **Accept**.
4. **OK**. If the IDE asks to restart, **Restart IDE**.

## 3. First-time setup (once)

Skip this if your team did it for you: open the chat and ask something. If it
answers, you're done.

1. Open the chat once (**Tools → cmcoder → Open cmcoder Chat**). Without
   settings it says so, and it puts cmcoder in `%USERPROFILE%\.cmcoder\bin`
   (Windows) or `~/.cmcoder/bin` (macOS, Linux): the one program for the
   terminal and every IDE.
2. In a terminal (the IDE's own **Terminal** tool window will do), run:

   - Windows, Command Prompt: `"%USERPROFILE%\.cmcoder\bin\cmcoder.exe" setup`
   - Windows, PowerShell: `& "$env:USERPROFILE\.cmcoder\bin\cmcoder.exe" setup`
   - macOS, Linux: `~/.cmcoder/bin/cmcoder setup`

   (`cmcoder setup`, if you installed the terminal package.) It asks for the
   gateway's address (from your AI team) and your API key (saved in your
   operating system's keychain, never in a file; it isn't shown), and lets you
   pick the models from the gateway's list.
3. Click **Restart** in the chat.

More (Open WebUI, certificates, proxies, doing it by hand): [setup.md](setup.md).

## 4. Use

1. Open your project. When the IDE asks whether to trust it, answer as you
   would anyway (see [Project trust](#project-trust)).
2. Click **cmcoder** on the right edge of the window, or **Tools → cmcoder →
   Open cmcoder Chat**. The chat opens on the right.
3. Type what you want and press **Enter** (**Shift+Enter** for a new line).
   For example: "explain this class", "add a unit test for parse",
   "any problems in this file?".

cmcoder reads files on its own. **Before it changes a file or runs a command,
it asks you.** It works in the project's folder; each open project has its
own chat.

### Review a change

The proposed change opens in the IDE's diff viewer, in a window titled
"cmcoder wants to change …": the file now on the left, the proposal on the
right. Choose **Accept**, **Accept Always** or **Reject** at the bottom, or
answer in the chat: **Allow**, **Always allow** (this kind of action in this
project, from now on) or **Deny** (with a note telling cmcoder what to do
instead). Closing the window answers nothing: the chat still asks.

![A proposed change in IntelliJ IDEA's diff viewer](screenshots/intellij-2-diff.png)

### Everyday actions

| To | Do |
|---|---|
| Ask about some code | select it, then **Alt+Shift+K** (Mac: **⌥⇧K**; not Ctrl+Alt+K as in the other IDEs, which is Commit and Push here) or right-click → **Ask cmcoder About Selection** |
| See what goes with your message | the 📎 line above the input: the open file, the selection and its problems. Untick it to leave them out once |
| Show an image (a screenshot, an error dialog) | **Ctrl+V** in the input, drop the file on the chat, or the **Image** button ([images.md](../features/images.md)) |
| Stop cmcoder | **Esc**, **Stop**, or **Tools → cmcoder → Stop cmcoder** |
| Start over | **Tools → cmcoder → New cmcoder Conversation** |
| Go back to an earlier conversation | **History** at the top of the chat |
| Change how much it asks | the picker at the top: **default** (ask) / **acceptEdits** (edits without asking) / **auto** (asks only for risky actions) / **plan** (read only). Read-only commands (`grep`, `find`, `cat`, `git log` …, piped too) never ask |
| A second check of each answer | tick **Critique** at the top |
| See background helpers (subagents) | **Tools → cmcoder → Open cmcoder Agent Navigator** (an editor tab) |
| Search code by meaning (big projects) | **Code search** in the status bar at the bottom, or **Tools → cmcoder → cmcoder Code Search…** ([code-search.md](../features/code-search.md)) |
| See what cmcoder did | the **Log** tab next to **Chat** in the cmcoder tool window |

All of these are under **Tools → cmcoder** too, and in **Find Action**
(**Ctrl+Shift+A**, Mac: **⇧⌘A**): type "cmcoder".

![The Agent Navigator in IntelliJ IDEA](screenshots/intellij-3-navigator.png)

*The Agent Navigator: the current turn and any helpers it started.*

Closing the chat stops cmcoder; opening it starts a new one. Your
conversations are kept (**History**).

## Settings

**File → Settings → Tools → cmcoder** (Mac: **IntelliJ IDEA → Settings… →
Tools → cmcoder**), or **Tools → cmcoder → cmcoder Settings…**:

| Setting | Meaning |
|---|---|
| cmcoder program | **leave empty**: the copy inside the plugin is used (or a newer shared copy) |
| Permission mode at start | ask / accept edits / auto / plan (read only) |
| Send the open file, the selection and its problems with each message | (on) |
| Show proposed changes in the diff viewer | (on) |

Changes apply the next time cmcoder starts (**New cmcoder Conversation**, or
close and reopen the chat).

### Project trust

A project can carry its own cmcoder settings (a `.cmcoder` folder: rules,
tools, MCP servers). cmcoder uses them **only when the IDE trusts the
project**, the choice the IDE asks for when you open a project from
somewhere new. In an untrusted project cmcoder still works, with your own
settings only. To change your answer: **File → Settings → Build, Execution,
Deployment → Trusted Locations**, then reopen the chat.

## Update or remove

- **Update:** install the new `.zip` the same way; the IDE replaces the old
  version.
- **Remove:** **Settings → Plugins → Installed →** cmcoder **→** the arrow
  next to **Disable** **→ Uninstall**, then restart if asked. Your settings,
  key and conversations stay in `~/.cmcoder`.

## If something's wrong

| Problem | Fix |
|---|---|
| "Plugin … is incompatible" / not compatible with this version | the IDE is older than 2024.2: update the IDE |
| The chat says the IDE's runtime has no built-in browser (JCEF) | **Help → Find Action → "Choose Boot Java Runtime for the IDE"**, pick a JetBrains Runtime with JCEF, restart |
| The program can't start | empty **cmcoder program** in the settings; or the file is another platform's: install the one for your computer |
| **Alt+Shift+K** does something else | another plugin or your keymap uses it: **Settings → Keymap**, search "Ask cmcoder", **Add Keyboard Shortcut** |
| "Can't reach the gateway" | connect the VPN; check `baseUrl` in your settings file |
| "No API key" / 401 | run `login` again (step 3); keys expire |
| Certificate error | add your company's root certificate: [setup.md](setup.md) |
| Commands fail on Windows: "Git Bash not found" | install Git for Windows, restart the IDE |
| Remote development (JetBrains Gateway, a remote backend) | not supported yet: the plugin runs where the IDE's backend runs, so use it in a local IDE |

For help, use **Tools → cmcoder → Copy cmcoder Diagnostics** and the **Log**
tab (**Tools → cmcoder → Show cmcoder Log**), and send both (your key is
never in them). More: [troubleshooting.md](troubleshooting.md).
