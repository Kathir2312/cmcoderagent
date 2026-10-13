# Phase 3: install and try it by hand (Windows)

About 30 minutes. You need what Phase 2 needed: Git for Windows, uv, your
gateway settings in `~/.cmcoder/settings.json`, and VS Code.

## 1. Install the Phase 3 version of cmcoder

In PowerShell, in your clone of the repository:

```powershell
git fetch origin
git checkout claude/agent-cli-vscode-parity-t3dm9z
git pull
uv sync                                   # the test script uses this Python for its MCP server
uv tool install --force --reinstall .     # puts this version's `cmcoder` on PATH
cmcoder --version
cmcoder doctor                            # your gateway settings from Phase 1/2 still apply
```

If `cmcoder` isn't found afterwards, run `uv tool update-shell` and open a
new terminal. The VS Code extension starts the same `cmcoder`, so this step
updates both.

## 2. Install the VS Code extension

Either:

- **Download it:** GitHub → Actions → the latest green CI run on this branch
  → Artifacts → `cmcoder-vsix` (a zip with `cmcoder.vsix` inside), or
- **Build it** (Node 22, or Node 20 with the older packaging tool):

  ```powershell
  cd vscode
  npm ci
  npm i -D @vscode/vsce@3.2.1               # only on Node 20
  node esbuild.mjs --production
  npx vsce package --skip-license --out cmcoder.vsix
  cd ..
  ```

Then: `code --install-extension cmcoder.vsix --force` (or Extensions view →
`...` → Install from VSIX), and reload VS Code.

## 3. Make the test project

```powershell
powershell -ExecutionPolicy Bypass -File docs\phase3\try-phase3.ps1
```

It makes `%TEMP%\cmcoder-phase3` (or the folder you give with `-Path`) with:

| What | Where |
|---|---|
| `app.py` with a bug (`add` subtracts) | the project |
| slash command `/explain <file>` | `.cmcoder\commands\explain.md` |
| skill `haiku` | `.cmcoder\skills\haiku\SKILL.md` |
| hook: `echo edited >> hook.log` after each edit | `.cmcoder\settings.json` |
| MCP server `tickets` (tool `get_ticket`) | `.mcp.json` + `tickets_server.py` |
| subagent `reviewer` (Read, Grep, Glob) | `~\.cmcoder\agents\reviewer.md` (yours; kept if you already have one) |

## 4. Terminal

```powershell
cd $env:TEMP\cmcoder-phase3
```

| # | Do | Expect |
|---|---|---|
| 1 | `cmcoder trust` | Lists the hook (`hooks (PostToolUse)`) and `.mcp.json: mcpServers (tickets)`. Answer **y**. |
| 2 | `cmcoder doctor --no-probe` | Sections **MCP servers** (`tickets`, project), **Hooks** (`PostToolUse [Edit\|Write]: echo edited >> hook.log`, "asks before it first runs") and **Commands, agents and skills** (`/explain`, `agent reviewer`, `skill haiku`). |
| 3 | `cmcoder`, type `/ex` and press **Tab** | Completes to `/explain`. Finish it as `/explain app.py` and press Enter. |
| 4 | (first message only) | A prompt to **start MCP server tickets**, showing what it runs (your Python and `tickets_server.py`). Allow it. Then the explanation. |
| 5 | `/help` | A "Your commands" part listing `/explain`. |
| 6 | `/mcp` | `tickets (project): connected · 1 tools`, and `tickets tools: get_ticket`. |
| 7 | `Use the reviewer agent to review app.py` | `● Task(reviewer: ...)`, then indented `│ ● Read(...)` lines (the subagent's steps), then a report that finds the `a - b` bug. |
| 8 | `write a poem about app.py` | `● Skill(haiku)` before the poem. |
| 9 | `What is ticket ABC-42? Use the tickets server.` | A permission prompt for `mcp__tickets__get_ticket`. Allow: the answer is "Add a --version flag". |
| 10 | `fix the add function in app.py` | The usual Edit prompt; after the edit, a prompt "Run this project's PostToolUse hook" showing `echo edited >> hook.log`. Allow both. |
| 11 | `/exit`, then `Get-Content hook.log` | `edited` (one line per edit). |
| 12 | `cmcoder -p "/explain app.py"` | The explanation printed, without the interactive UI (commands work in `-p` too). |

Optional: `cmcoder --tui`, type `/ex`: the rest of the name appears in grey;
the Right arrow key accepts it.

## 5. VS Code

Open the folder (`code $env:TEMP\cmcoder-phase3`), answer **Yes, I trust the
authors**, and open the cmcoder panel.

| # | Do | Expect |
|---|---|---|
| 1 | Type `/` in the input box | A list: `/compact`, `/mcp`, `/explain <file> ... (project)`. Arrow keys move, Tab or Enter picks. |
| 2 | `/explain app.py` | The explanation. |
| 3 | `/mcp` | A reply with the servers' state (`tickets ... connected`). Nothing is sent to the model. |
| 4 | `Use the reviewer agent to review app.py` | A `Task(reviewer: ...)` card listing the subagent's steps while it works; when it finishes they fold into "N subagent steps" (click to open). **It must not freeze** (that was the Windows bug fixed in `4f2c1cb`). |
| 5 | `What is ticket ABC-42? Use the tickets server.` | A permission card for `mcp__tickets__get_ticket`; Allow; the title in the answer. |
| 6 | `fix the average function so an empty list returns 0` | The diff editor and permission card as in Phase 2; `hook.log` gets another line. |

The MCP server and the hook don't ask again in VS Code if you approved them
in the terminal: approvals are remembered until their settings change.

## 6. Tell me the result

All good: say so, and I'll write `STATUS.md` and close Phase 3 (then tag
`phase3` like the earlier phases). Something wrong: the row number, a
screenshot or the error text, and the output of `cmcoder doctor --no-probe`.
For MCP problems, the server's own output is in
`~\.cmcoder\logs\mcp-tickets.log`.

## 7. Clean up afterwards

```powershell
cd $env:TEMP\cmcoder-phase3; cmcoder trust --revoke; cd ~
Remove-Item -Recurse -Force $env:TEMP\cmcoder-phase3
Remove-Item ~\.cmcoder\agents\reviewer.md     # if the script made it
```
