# Sessions, rewind and long conversations

## Saved conversations

Conversations are saved per project, so you can stop and carry on later:

| Want | Terminal | VS Code |
|---|---|---|
| Continue the latest conversation | `cmcoder -c` | **cmcoder: Continue Last Conversation** |
| Pick an earlier one | `/resume` (a list), `/resume <n>`, or `cmcoder -r <id>` | **Open in Terminal**, then `/resume` |
| Start fresh | `/clear` | **cmcoder: New Conversation** |

The other IDEs start over with **New Conversation** (Visual Studio, Eclipse,
NetBeans, the JetBrains IDEs: see each guide in [../guides/](../guides/README.md));
their conversations are saved the same way.

The files are under `~/.cmcoder/projects/` (owner-only), and are deleted after
`cleanupPeriodDays` (default 30). `"persistSessions": false` turns saving off.
With `smallFastModel` set, each conversation gets a title written by the
small model (shown by `/resume`).

The terminal and every IDE share them: a conversation started in VS Code can
be resumed in the terminal.

## Rewind

`/rewind` goes back to one of your earlier messages and undoes, as you choose,
the agent's **file changes** since then (made with Write and Edit), the
**conversation**, or both. Changes made by commands in Bash are not undone:
**commit to git before a big run**.

Subagents' file changes are undone too ([agents.md](agents.md)).

## The todo list

For work with several steps the agent keeps a todo list (TodoWrite), shown as
a ☑ ► ☐ checklist; `/todos` shows it again.

## Long conversations: the context window

Qwen3 is often served with a 32K-token window, which an agent fills quickly.
cmcoder sizes each request to the room left and pages long files. When the
conversation reaches 80% of the window it **summarises the older part** (with
`smallFastModel` if set) and keeps the recent messages.

| Want | How |
|---|---|
| Summarise now, keeping something in focus | `/compact [what to keep]` |
| Start fresh | `/clear` |
| Change when it happens | `"autoCompact": true`, `"autoCompactThreshold": 0.8` |
| Set the window yourself | `"modelProfiles": [{"match": "qwen3*", "contextWindow": 40960}]` ([gateway.md](gateway.md#model-profiles)) |

If summarising fails, the oldest tool outputs are dropped instead. A
`PreCompact` hook runs before each summary ([hooks.md](hooks.md)).
`cmcoder doctor` shows the window and where it came from.

**Tip:** one feature per conversation goes well with a 32K window; "build the
whole application" gets summarised too often and quality drops.

## Running without a chat

```bash
cmcoder -p "fix the failing test" --permission-mode acceptEdits --allowedTools "Bash(pytest:*)"
git diff | cmcoder -p "review this diff"
cmcoder -p "..." --output-format json         # or stream-json (one event per line)
```

`-p` runs one request and exits (scripts, CI). It never asks: what isn't
allowed is refused, and high-risk commands are always refused.

## Symbols on Windows

The classic Windows console (PowerShell or cmd in their own window) shows `?`
for characters its font lacks. There cmcoder uses a basic set: an ASCII
spinner, marks from the console's code page, and ASCII stand-ins for the rest
(`…` → `...`, `●` → `*`, `☐` → `[ ]`). It's chosen automatically; Windows
Terminal, the VS Code terminal, ConEmu and Git Bash get the full set. Override
with `"symbols": "basic"` or `"unicode"` (or `CMCODER_SYMBOLS`);
`cmcoder doctor` says which set is in use.

## The full-screen terminal UI

`cmcoder --tui` (or `"ui": "textual"`) opens a full-screen UI with a status
bar, a fixed-size permission dialog and the agent navigator (Ctrl+G).
`/resume` and `/rewind` are in the classic terminal UI for now.
