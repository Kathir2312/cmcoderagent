# cmcoder in the terminal

The terminal package is the cmcoder program plus install scripts. It contains
everything it needs: **no Python, pip, uv or Node.js** is needed or used.

## Install

1. Unzip `cmcoder-terminal-<platform>.zip` anywhere (for example Downloads).
2. Run the install script inside it:

   | Windows | macOS, Linux |
   |---|---|
   | double-click **`install.cmd`** (or `powershell -ExecutionPolicy Bypass -File install.ps1`) | `sh install.sh` |
   | installs into `%LOCALAPPDATA%\Programs\cmcoder` and adds it to your PATH | installs into `~/.local/share/cmcoder` and links `~/.local/bin/cmcoder` |

   No administrator rights are needed.
3. Open a **new** terminal (the old one doesn't see the new PATH) and run:

   ```
   cmcoder doctor
   ```

4. First time only: [setup.md](setup.md) (your gateway and `cmcoder login`).

If your company blocks scripts: copy the `cmcoder` folder from the zip
anywhere you like and add that folder to your PATH yourself.

**Update:** run the install script of the new version (your settings, saved
conversations and key stay). **Uninstall:** `uninstall.cmd` (Windows) or
`sh uninstall.sh`; your settings, conversations and key are kept.

On Windows, cmcoder runs shell commands with Git Bash: install Git for Windows
if `cmcoder doctor` says it's missing.

## Use

```
cd C:\work\my-project
cmcoder
```

Type what you want ("explain how login works", "add a test for parse_date",
"why does the build fail?") and press **Enter**. cmcoder reads files on its
own; before it changes a file or runs a command it asks:

- **Yes**: this once.
- **Always**: this kind of action in this project, from now on.
- **No**: and you can tell it what to do instead.

| Key | Does |
|---|---|
| **Enter** | send |
| **Alt+Enter** (or Esc then Enter) | new line |
| **Shift+Tab** | cycle the permission mode: ask / accept edits / plan (read only) |
| **Alt+V** (or Ctrl+V) | paste an image from the clipboard; dragging an image file into the terminal attaches it too ([images.md](images.md)) |
| **Ctrl+C** | stop the current answer; twice to quit |

### Slash commands (type them in the chat)

| Command | Does |
|---|---|
| `/help` | everything below |
| `/clear` | a new conversation |
| `/resume [n\|id]` | continue an earlier conversation (a list without an argument) |
| `/rewind` | go back to an earlier message, undoing the file changes made since |
| `/todos` | cmcoder's current task list |
| `/compact [focus]` | shorten a long conversation to save room |
| `/model [name]` | show or change the model |
| `/mode [mode]` | show or change the permission mode |
| `/cost` | tokens used |
| `/mcp` | your MCP servers and their state |
| `/index [status]` | update the code search index, or show its state |
| `/agents [n\|stop n\|map]` | background helpers (subagents): list, show, stop, map |
| `/critic [on\|off]` | a second check of answers before you see them |
| `/exit` | quit |

### Start options

| Option | Does |
|---|---|
| `cmcoder -c` | continue the last conversation in this folder |
| `cmcoder -r` | pick an earlier conversation to resume |
| `cmcoder -p "question"` | answer once and exit (for scripts); `--output-format json` for machine-readable output |
| `-m NAME` | use another model |
| `--permission-mode plan` | start read only (`default`, `acceptEdits`, `plan`) |
| `--allowedTools "Bash(git status)"` / `--disallowedTools Bash` | allow or forbid tools for this run |
| `--trust-project` | use this project's own `.cmcoder` settings (only for projects you trust) |
| `--tui` | the full-screen interface |

### Other commands

| Command | Does |
|---|---|
| `cmcoder doctor` | checks the gateway, network, certificates, key and model; says what to fix |
| `cmcoder login` / `logout` | store / remove your API key (in the OS keychain) |
| `cmcoder models` | the models the gateway offers |
| `cmcoder trust` | trust the current project's own settings |
| `cmcoder index` | build the code search index for this project |
| `cmcoder rag setup` / `status` / `on` / `off` | code search: set up, state, switch on/off |
| `cmcoder mcp add/list/remove` | MCP servers (more tools) |
| `cmcoder terminal-profile` | add a "cmcoder" profile to Windows Terminal |
| `cmcoder version` | the version |

## Code search (optional)

For big projects, `cmcoder rag setup` lets cmcoder find code by meaning:
[code-search.md](code-search.md).

## Without Python: what to avoid

The program itself never needs Python. Some things *you* configure can:

- **MCP servers started with `npx`, `uvx` or `python`** need Node.js or
  Python on your PC. Prefer servers with a URL
  (`cmcoder mcp add NAME --url https://…`).
- **Hooks** run the command you write; write them for Git Bash / PowerShell,
  not as Python scripts, unless Python is installed.

More: [troubleshooting.md](troubleshooting.md).
