# Phase 5: try code search by hand (Windows)

About 45 minutes. You need what Phase 4 needed, plus an **embedding model on
your gateway** (ask the LiteLLM admin, or on Open WebUI's Ollama server:
`ollama pull nomic-embed-text`). Parts A–D use LiteLLM; part E Open WebUI;
part F (a Chroma server) is optional.

Note the row number of anything that doesn't match "Expect".

## A. Install

```powershell
cd <your clone>
git fetch origin; git checkout claude/agent-cli-vscode-parity-t3dm9z; git pull
uv sync
uv tool install --force --reinstall .
cmcoder rag --help        # works only on the Phase 5 version
```

VS Code: the `cmcoder-vsix` artifact of the latest green CI run, as in
Phase 4 (`code --install-extension cmcoder.vsix --force`, reload).

Test project: a real repository you know (medium size is best: a few
hundred files), so you can judge the answers. Below it's `C:\work\myrepo`.

## B. Set it up in the terminal

```powershell
cd C:\work\myrepo
```

| # | Do | Expect |
|---|---|---|
| B1 | `cmcoder rag setup` | 1: a numbered list of your gateway's models, embedding-looking ones first. Pick yours: "✓ … answers (N dimensions)". 2: pick "On this machine, built in". 3: pick "Mine". 4: "Index this project now?" → y |
| B2 | (indexing) | A progress bar, then "Indexed N files (M pieces) in …s". Time it: roughly a minute per few hundred files, depending on the gateway |
| B3 | `cmcoder index` | "Indexed 0 files … N unchanged" (nothing embedded again) |
| B4 | `cmcoder index --status` | Model, "local index (C:\Users\…\.cmcoder\index\…)", files, pieces, "Updated just now", automatic context on |
| B5 | `cmcoder doctor` | **Code search (RAG)**: the embedding model answers; "This project's index: N files…" |
| B6 | Check secrets aren't indexed: if the repo has a `.env`, ask in B8 about something only in it | Not found by code search |

## C. Use it in a session

| # | Do | Expect |
|---|---|---|
| C1 | `cmcoder`, then ask about something by **what it does**, not its name (e.g. "where do we retry failed payments?") | A dim line "◦ Added code from the index: path:lines…" before the answer; the answer names the right code |
| C2 | Ask something that needs looking around ("which modules call the payment API and how?") | The model calls `● CodeSearch("…")` (and maybe Grep/Read); results show file:lines |
| C3 | Ask it to change something small in a file it found, allow the edit, then ask about the new code by meaning | The new code is found (the edit was re-indexed before the search) |
| C4 | Ask the same question as C1 again | No second "Added code" line for the same pieces (sent once per conversation) |
| C5 | `hi` | No code added (too short) |
| C6 | `/index status` then `/index` | The status; then "Indexed 0 files… N unchanged" (or the files you changed) |
| C7 | `/rewind`, pick the C1 message | The message comes back **without** the added code |
| C8 | Edit a file in your editor (outside cmcoder), then ask about it by meaning | The answer reflects your edit |
| C9 | `cmcoder rag off`, start `cmcoder`, ask C1 again | No code added, no `CodeSearch`. Then `cmcoder rag on` |

## D. VS Code

Open the repo in VS Code and the cmcoder panel.

| # | Do | Expect |
|---|---|---|
| D1 | Look at the status bar | "$(search) Code search: N files" |
| D2 | Ask C1's question in the panel | A note "◦ Added code from the index: …" above the answer |
| D3 | `/index` in the panel | A "Code index" reply; the status bar spins briefly |
| D4 | Click the status bar item → "Show the index" | The same lines as `cmcoder index --status` |
| D5 | Open another folder **without** an index, open the panel | Status bar: "Code search: not indexed"; click → Update → it indexes (spinner with file counts), then "Code search: N files" |
| D6 | Command Palette → "cmcoder: Set Up Code Search" in a folder | The 4 steps as pick lists; at the end a message "Code search uses …"; the next message gets code context **without restarting** |

## E. Open WebUI (skip if you have none)

| # | Do | Expect |
|---|---|---|
| E1 | `cmcoder rag setup`, pick an embedding model listed under your Open WebUI provider (e.g. `webui:nomic-embed-text:latest`) | "✓ … answers (768 dimensions)" (for nomic) |
| E2 | Pick a model name that doesn't exist (choose "Another" and type `webui:nope`) | "Open WebUI has no model 'nope'…" with a hint, not a bare error 500 |
| E3 | Index and repeat C1–C2 with a chat model on Open WebUI | Same behaviour as with LiteLLM |

## F. A Chroma server (optional)

Read [SECURITY-REVIEW.md](SECURITY-REVIEW.md) finding 2 before deploying one
for a team. For a quick local try: `uv tool install chromadb` then
`chroma run --path C:\chroma-data --port 8000` in another terminal.

| # | Do | Expect |
|---|---|---|
| F1 | `cmcoder rag setup`: store "A Chroma server", `http://localhost:8000`, no key, "search and update", scope Mine, index now | "✓ The index will be kept in the Chroma server …"; indexing as before |
| F2 | `cmcoder index --status` | "Chroma server http://localhost:8000 (collection cmcoder-<repo>-…)" |
| F3 | C1 again | Code added as before |
| F4 | Stop the Chroma server, ask C1 again | One warning "Code search isn't answering…", and the answer still comes |
| F5 | Set it back to the built-in store: `cmcoder rag setup` again (store: on this machine) | |

## G. Subagents in parallel (the fix from 5 October)

In a solution with several projects (e.g. your .NET solution), in VS Code and
then in the terminal:

| # | Do | Expect |
|---|---|---|
| G1 | "Use one explore subagent per project to analyse it, then give me one summary of all" | Several Task cards **working at the same time** (their steps grow together); then one summary |
| G2 | The same in the terminal (`cmcoder`) | The agent map under the spinner (G7); with `cmcoder -v`, every step too, tagged `│ [Analyse TW.Core] ● Read(…)` |
| G3 | Set `"subagentMaxTurns": 5` in `~\.cmcoder\settings.json`, ask G1 with `general-purpose` subagents | "⚠ [general-purpose] Reached 5 model calls (max turns); asking for its report." and a report (not half a sentence); the card says "step limit reached". Remove the setting afterwards |
| G4 | Ask for two `general-purpose` subagents that each need a permission (e.g. each runs a different `dotnet build`) | The questions come one after the other, never two at once; deny one: both subagents stop |
| G5 | During G1 in VS Code: the **Subagents** box above the input | One row per subagent: ◐/⏸/○/✓, steps of the limit, tools, tokens, a ticking time, and what it's doing; each Task card has a status line |
| G6 | Click **Stop** on one row | It turns "stopping", then "stopped"; the others carry on; the main answer says that part may be incomplete |
| G7 | In the terminal during G1: Ctrl+C | "Stop a subagent?" with the running ones; type a number: that one stops, the turn goes on. Ctrl+C again then `a`: everything stops |
| G8 | After the turn: `/agents`, then `/agents 1` | The agent types and this session's runs; then run 1's steps and report |
| G9 | VS Code: send any task | Above the input: an animated ✻ with a word ("Considering…"), then "Running… Bash(…)" etc., the seconds ticking, "↓ N tokens", "Esc to interrupt" |
| G10 | While it works, type another message and press Enter | It shows as "↳ …" above the input (× removes it); when the turn ends it is sent by itself. Esc instead: it comes back into the input |
| G11 | During G1: click **Map** in the Subagents box (or "cmcoder: Open Agent Navigator") | An editor tab beside the chat: main agent → subagents (coloured by state, links moving while they run) → their last 3 tool calls; click one: its steps and report on the right; Stop works from the node |
| G12 | Terminal during G1 (a wide window) | Under the spinner, a mind map: main agent → subagents (state, steps, tools, tokens, time) → each one's last 3 tool calls, the current one coloured. After the turn: `/agents map` shows it again |
| G13 | `cmcoder --tui` during G1 | The same map above the input; Ctrl+G: the navigator (arrow keys through the tree, details on the right, S stops the selected subagent, Esc back) |

## Tell me the result

All good: say so, and I'll write `STATUS.md`. Something wrong: the row
number, a screenshot or the error text, and `cmcoder doctor`'s output. Also
useful: the result of

```powershell
uv run python evals/retrieval.py
```

(in your clone; it uses your embedding model) to see how well it finds
cmcoder's own functions.

## Clean up

```powershell
cmcoder index --clear          # in each project you indexed
cmcoder rag off                # or remove "rag" from ~\.cmcoder\settings.json
```
