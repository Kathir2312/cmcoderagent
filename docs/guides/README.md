# cmcoder guides: install and use

cmcoder is a coding assistant: you ask in plain language, and it reads your
project, explains code, and proposes changes and commands, which you approve.
The model runs on your company's AI gateway (LiteLLM or Open WebUI); cmcoder
runs on your PC.

> Your company may have renamed it (its own name and icon). Everything here is
> the same; only the name differs.

## Which one?

| You work in | Install | Guide |
|---|---|---|
| A terminal (PowerShell, cmd, Terminal, bash) | the terminal package | [terminal.md](terminal.md) |
| VS Code | the VS Code extension | [vscode.md](vscode.md) |
| Eclipse | the Eclipse plugin | [eclipse.md](eclipse.md) |

You can use several: they share your settings, your API key, your permission
answers and your saved conversations. Each IDE extension contains its own copy
of cmcoder, so you don't need the terminal package for them.

Then, once, whichever you chose: [setup.md](setup.md) (your gateway and API
key). Problems: [troubleshooting.md](troubleshooting.md).

## What your PC needs

**No Python.** Nothing you install here needs Python, pip, uv or Node.js, and
nothing asks you to install them. cmcoder is a standalone program inside each
download.

| You need | Why | Who |
|---|---|---|
| **Git for Windows** | cmcoder runs shell commands with Git Bash (as Claude Code does) | Windows only; most developers have it. macOS and Linux have bash already |
| **Microsoft Edge WebView2 Runtime** | the chat panel in Eclipse (VS Code has its own) | Windows; part of Windows 11 and of Microsoft Edge |
| **Java 21** | runs Eclipse itself | Eclipse users (Eclipse 2024-06 or newer) |
| **WebKitGTK** (`libwebkit2gtk-4.1-0`) | the chat panel in Eclipse | Eclipse on Linux |
| Network access to your AI gateway | the model | everyone (often: the VPN) |

## Where the files come from

Your team shares them (or: GitHub → Actions → **Release build** → the latest
green run → Artifacts). Pick your platform: `win32-x64` (Windows),
`darwin-arm64` (Mac with Apple silicon) or `linux-x64`.

| File | What |
|---|---|
| `cmcoder-terminal-<platform>.zip` | the terminal package (install scripts inside) |
| `cmcoder-<platform>.vsix` | the VS Code extension (cmcoder inside) |
| `cmcoder-eclipse` (a `.zip`) | the Eclipse plugin, every platform in one file |

Updates are new files: install the new one over the old one.
