# Troubleshooting

Start with the check: `cmcoder doctor` in a terminal (without the terminal
package: the extension's own cmcoder, see [setup.md](setup.md)), or in Eclipse Ctrl+3 →
**cmcoder: Copy Diagnostics**. It names the problem and the fix. Your API key
is never shown in it.

## PCs without Python

Nothing in the terminal package, the VS Code extension or the Eclipse plugin
needs Python, and cmcoder never tries to install it. If something asks for
`python`, `pip`, `uv` or `npx`, it comes from your own configuration:

| You see | Cause | Fix |
|---|---|---|
| An MCP server shows "failed" in `/mcp` (`'npx'`/`'uvx'`/`'python'` not found) | the server is started with Node.js or Python | use the server's URL instead: `cmcoder mcp add NAME --url https://…`, or ask your team for a hosted one |
| A hook fails with "command not found" | the hook is a Python script | write it for Git Bash (Windows) / sh, or remove it |
| "Chroma needs its URL" | `"store": {"type": "chroma"}` without a `url` | `cmcoder rag setup` → **built-in** or **Chroma, by its URL** ([code-search.md](code-search.md)) |
| "Can't reach the Chroma server http://localhost:…" | Chroma isn't running on this PC | start it ([code-search.md](code-search.md)) |
| VS Code: "cmcoder couldn't start" mentioning `uv` | `cmcoder.executable` / `executableArgs` were set | remove both settings: the extension then uses its own copy |
| Eclipse: the program can't start | **cmcoder program** preference set | empty it (Preferences → cmcoder) |

## Common problems

| Problem | Fix |
|---|---|
| `cmcoder` not found (terminal) | open a **new** terminal after installing; check `%LOCALAPPDATA%\Programs\cmcoder` is on PATH (Windows) or `~/.local/bin` (macOS, Linux) |
| Commands fail on Windows: "Git Bash not found" | install Git for Windows, restart the IDE / terminal |
| "Can't reach the gateway" / name not found | connect the VPN; check `baseUrl` in `~/.cmcoder/settings.json` |
| Certificate error | IT's root certificate isn't in the Windows/macOS store: add `"caCertPath"` to the provider ([setup.md](setup.md)). Never switch certificate checks off |
| Proxy errors | set `HTTPS_PROXY`; put internal gateway domains in `NO_PROXY` |
| "No API key" / 401 | `cmcoder login` again (keys expire); for Open WebUI an API key, not a password |
| Model not found | `cmcoder models` lists the gateway's names; fix `model` in your settings |
| Eclipse: the chat is blank (Windows) | install the Microsoft Edge WebView2 Runtime |
| Eclipse: the chat is blank (Linux) | install WebKitGTK: `sudo apt install libwebkit2gtk-4.1-0` (or your distribution's package) |
| Eclipse: plugin won't install | Eclipse older than 2024-06, or not on Java 21 |
| The project's `.cmcoder` settings are ignored | the project isn't trusted: `cmcoder trust`, VS Code's workspace trust, or Eclipse's preference |
| Corporate antivirus blocks `cmcoder.exe` | ask IT to allow it (the signed build, once your company signs it) |

## Logs

| Where | How |
|---|---|
| VS Code | **cmcoder: Show Log** |
| Eclipse | Ctrl+3 → **cmcoder: Show Log** (Console view) |
| Terminal / all | `~/.cmcoder/logs/` (MCP servers: `mcp-<name>.log`) |

When asking for help, send the **diagnostics** text and the log, not
screenshots of your settings: they're safe to share (no key in them).
