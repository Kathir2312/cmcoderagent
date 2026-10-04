# Phase 4: install and try it by hand (Windows, plus WSL2 for the sandbox)

About 60–90 minutes. Parts A–D need only Windows; part E (the sandbox) needs
WSL2; parts F–G are optional. You need what Phase 3 needed (Git for Windows,
uv, VS Code, your LiteLLM settings in `~\.cmcoder\settings.json`), and for
part C an Open WebUI account if your company has one.

Note the row number of anything that doesn't match "Expect".

## A. Install the Phase 4 version

In PowerShell, in your clone of the repository:

```powershell
git fetch origin
git checkout claude/agent-cli-vscode-parity-t3dm9z
git pull
uv sync
uv tool install --force --reinstall .
cmcoder --version
```

VS Code extension: GitHub → Actions → the latest green **CI** run on this
branch → Artifacts → `cmcoder-vsix` → unzip → `code --install-extension
cmcoder.vsix --force`, then reload VS Code. (Part D tests the other kind, the
one with cmcoder inside.)

Test project:

```powershell
$p = "$env:TEMP\cmcoder-phase4"; mkdir $p -Force | Out-Null; cd $p
git init -q
Set-Content app.py "def add(a, b):`n    return a - b`n`n`ndef average(xs):`n    return sum(xs) / len(xs)`n"
git add -A; git -c user.name=t -c user.email=t@example.com commit -qm start
```

## B. LiteLLM: nothing broke, and the Phase 4 changes

| # | Do | Expect |
|---|---|---|
| B1 | `cmcoder doctor` | As in Phase 3, plus a **Bash sandbox** section: "Not available on native Windows: run cmcoder inside WSL2…" (a note, not an error). No Telemetry section (it's off). |
| B2 | `cmcoder` | The **classic** UI starts (not the full-screen one); the header shows the logo, the name `cmcoder`, your model and gateway. The terminal tab's title is "cmcoder · cmcoder-phase4". |
| B3 | `fix the add function in app.py` | The usual Edit prompt; allow; `app.py` fixed. |
| B4 | `/exit`, then `cmcoder --tui` | The full-screen UI, with the logo and name at the top. `Ctrl+D` quits. |

### VS Code panel (open the folder: `code $env:TEMP\cmcoder-phase4`)

| # | Do | Expect |
|---|---|---|
| B5 | Open the cmcoder panel | Before the first message: the **cmcoder icon and name** in the middle of the panel. The Extensions list shows the extension **with an icon**. |
| B6 | `what does average do?` | The answer, with a small cmcoder icon beside it. |
| B7 | `/help` | The list of commands, answered in the panel (nothing sent to the model). |
| B8 | `/cost` and `/todos` | Token use / cost so far; the todo list (or "no todos"). |
| B9 | `/model` then `/model <another model>` | The current model; then a switch, shown at the top of the panel. |
| B10 | `make average return 0 for an empty list` (allow the edit), then `/rewind` | VS Code pick lists: which message, then **code, conversation or both**. Pick the message and "both": the conversation is redrawn, `app.py` is back as before, and your message is back in the input box. |
| B11 | Command palette → "cmcoder: Open in terminal" | A terminal tab **with the cmcoder icon**, running cmcoder. |

## C. Open WebUI (skip if your company has none)

Follow [openwebui.md](openwebui.md) §1–2: an API key from Open WebUI
(Settings → Account), and in `~\.cmcoder\settings.json` a provider
`"webui": {"type": "openwebui", "baseUrl": "https://<your Open WebUI>"}`,
then `cmcoder login --provider webui`.

| # | Do | Expect |
|---|---|---|
| C1 | `cmcoder models --provider webui` | Open WebUI's models (no "arena" entries). |
| C2 | `cmcoder doctor --model webui:<a model>` | The model's **backend** (Ollama or OpenAI-compatible); for an Ollama model, the context window cmcoder sends as `num_ctx`; "native tool calling works" (if not: openwebui.md "If tools don't work"). |
| C3 | `cmcoder --model webui:<a model>` then `fix the add function in app.py` (use `git checkout app.py` first) | Read and Edit tool calls work, as with LiteLLM. |
| C4 | `read app.py and also list the files here` | Two tools in one turn (Ollama sends them numbered alike; they must stay separate). |
| C5 | VS Code: settings → `cmcoder.executableArgs` = `["--model", "webui:<a model>"]`, new conversation, B6 again | Works the same through the panel. Remove the setting afterwards. |

## D. The build with nothing else needed (no Python, no uv)

GitHub → Actions → the latest green **Release build** run → Artifacts:
`cmcoder-win32-x64` (a folder) and `cmcoder-win32-x64.vsix`. Unzip both.

| # | Do | Expect |
|---|---|---|
| D1 | Explorer → `cmcoder-win32-x64\cmcoder.exe` | The **cmcoder icon**. Right-click → Properties → Details: product name `cmcoder`, version 0.1.0. |
| D2 | In a new PowerShell: `& "<folder>\cmcoder.exe" doctor` | Works (it uses your same `~\.cmcoder` settings and key). |
| D3 | `code --install-extension cmcoder-win32-x64.vsix --force`, reload; make sure the setting `cmcoder.executable` is **empty**; open the panel and ask something | It answers. "cmcoder: Show Log" shows `Starting …\bin\cmcoder\cmcoder.exe --protocol stdio …`: the copy inside the extension, not the one on PATH. |
| D4 | `& "<folder>\cmcoder.exe" terminal-profile`, then open a **new** Windows Terminal window | The drop-down (˅) has a **cmcoder** profile with the icon; it starts cmcoder in your home folder. `cmcoder.exe terminal-profile --remove` removes it. |

Afterwards, reinstall the extension from part A if you want the normal one
back (`code --install-extension cmcoder.vsix --force`).

## E. The Bash sandbox (WSL2)

Once, in PowerShell: `wsl --install -d Ubuntu` (restart if asked), then in
the Ubuntu window:

```bash
sudo apt update && sudo apt install -y bubblewrap git curl
curl -LsSf https://astral.sh/uv/install.sh | sh && source ~/.bashrc
git clone <this repository's URL> cmcoderagent && cd cmcoderagent
git checkout claude/agent-cli-vscode-parity-t3dm9z
uv tool install --force .
mkdir -p ~/.cmcoder && cp /mnt/c/Users/$USER_WINDOWS/.cmcoder/settings.json ~/.cmcoder/   # or write it again
cmcoder login            # the key goes to Linux's keychain or file store, not the Windows one
```

(Replace `$USER_WINDOWS` with your Windows user name. Behind a company proxy,
set `HTTPS_PROXY` in WSL as you do on Windows.)

Test project in WSL:

```bash
mkdir -p ~/sbx && cd ~/sbx && git init -q && echo "print('hi')" > hello.py
git add -A && git -c user.name=t -c user.email=t@example.com commit -qm start
```

| # | Do | Expect |
|---|---|---|
| E1 | `cmcoder doctor` | **Bash sandbox**: "Bash commands run in a sandbox (bubblewrap)", network: none. If it says unprivileged user namespaces are blocked (Ubuntu 24.04), run the `sysctl` command it shows, then again. |
| E2 | `cmcoder`, then `run python3 hello.py and ls -la` | **No permission prompt** (sandboxed commands run straight away); the output. |
| E3 | `run: touch ~/outside.txt` | Fails ("Read-only file system"); `ls ~/outside.txt` (in another window) finds nothing. If the model then asks to run it outside the sandbox, a prompt marked **[outside the sandbox]** appears: **deny**. |
| E4 | `run: curl -sS https://example.com` | Blocked: "network access to example.com is blocked. Add it to sandbox.network.allowedHosts…". |
| E5 | `/exit`; add to `~/.cmcoder/settings.json`: `"sandbox": {"network": {"allowedHosts": ["example.com"]}}`; `cmcoder`, E4 again | The page's HTML comes back. |
| E6 | `add a line to hello.py, then commit it with git` | The edit works; `git commit` **fails in the sandbox** (`.git` is read-only), and the model asks to run it **outside the sandbox**: a prompt. Allow it: committed (`git log` shows it). |
| E7 | `run: ls /run; echo key=$CMCODER_API_KEY` | `/run` is empty, the key is empty (even if you set `CMCODER_API_KEY` before starting). |
| E8 | `run: mkdir .vscode` (the project has none) | Fails. After `/exit`, the project has no leftover empty `.vscode`, `.mcp.json` or `.cmcoder` folders that weren't there before. |

## F. Usage metrics (optional: if your company has an OpenTelemetry collector)

In `~\.cmcoder\settings.json` (or ask for managed settings):
`"telemetry": {"enabled": true, "endpoint": "https://<collector>:4318", "headers": {"Authorization": "Bearer ${OTEL_TOKEN}"}}`.

| # | Do | Expect |
|---|---|---|
| F1 | `cmcoder doctor` | "OpenTelemetry metrics go to …" and the collector accepted a test export. |
| F2 | Use cmcoder for a few messages, then look in your metrics tool | `cmcoder.session.count`, `cmcoder.token.usage`, `cmcoder.tool.calls`, `cmcoder.turn.count` with your model's name; **no prompt text, file names or commands** anywhere. |

Remove the `telemetry` block afterwards (it's off by default).

## G. Your company's icon and name (optional: needs Node 22)

See [branding.md](branding.md). Short version, in the repository:

```powershell
# put your logo in branding\icon.png (square, 256+ px, transparent),
# your one-colour side-bar icon in branding\icon-mono.svg, and edit
# branding\brand.json (productName, publisher, company, copyright)
uv run python packaging\brand.py                       # checks; lists any problem
uv run --with pyinstaller python packaging\build.py    # dist\cmcoder\
uv run python packaging\vsix.py                        # vscode\cmcoder-win32-x64.vsix
```

| # | Do | Expect |
|---|---|---|
| G1 | `brand.py` with a non-square or small image | It refuses and says why. |
| G2 | D1–D4 with your build | Your icon and name on `cmcoder.exe`, in VS Code (Extensions list, side bar, panel, terminal tab) and in the Windows Terminal profile; the terminal UI shows your name and colour. `git status` shows only your `branding\` changes (the packaging put `vscode\` back). |

## Tell me the result

All good: say so, and I'll write the guides and `STATUS.md` and close
Phase 4 (you tag `phase4`). Something wrong: the row number, a screenshot or
the error text, and the output of `cmcoder doctor`.

## Clean up afterwards

```powershell
cd ~; Remove-Item -Recurse -Force $env:TEMP\cmcoder-phase4
cmcoder terminal-profile --remove
```

In WSL: `rm -rf ~/sbx`, and the `sandbox` block in `~/.cmcoder/settings.json`
if you added it.
