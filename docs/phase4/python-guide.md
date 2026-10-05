# cmcoder Phase 4, explained for Python developers

Phase 4 made cmcoder something other developers can install and trust: a
second gateway (Open WebUI), a sandbox for the agent's shell, usage metrics, a
build that needs no Python, and the company's name and icon. This guide
follows [PLAN.md](PLAN.md) item by item, in the style of the
[Phase 3 guide](../phase3/python-guide.md).

---

## 1. Open WebUI gateway

*Code:* `src/cmcoder/providers/openwebui.py` (`OpenWebUIProvider`), the
Ollama options in `providers/openai_compat.py` (`build_request`), the window
in `cli/factory.py` (`_ollama_window`). *Tests:* `tests/test_openwebui.py`
(the mock server's Open WebUI mode), `tests/test_openwebui_real.py` (a real
Open WebUI, own CI workflow). *Guide:* [openwebui.md](openwebui.md).

### The problem

Some teams run Open WebUI instead of LiteLLM. It speaks almost the OpenAI
format, but not quite: its models may be Ollama models (with Ollama's habits)
or OpenAI-compatible ones, and it hides the settings cmcoder needs (the
context window).

### The idea

Reuse everything from the OpenAI-compatible provider and override only what
differs. Open WebUI's own source code (0.11) was read first, then checked
against a real Open WebUI with both kinds of backend.

### The code

- **`OpenWebUIProvider(OpenAICompatProvider)`**: `api_base()` turns the
  server's URL into `<url>/api`; the model list is `/api/models` without
  entries that aren't chat models (`owned_by`: `arena`, ...).
- **The context window.** Open WebUI won't say a model's `num_ctx`, and
  Ollama silently drops the *start* of a prompt longer than its window. So
  cmcoder sends `options.num_ctx` itself (its own window, capped at the
  model's trained length from `/ollama/api/show`) and skips the "too long"
  probe, because Ollama never refuses.
- **Small differences, each from the source:** `think: false` only for models
  known to think (Ollama rejects it otherwise); `num_ctx` only for Ollama
  models; errors are `{"detail": "..."}`.
- **A real bug found on the way:** through Open WebUI, Ollama's tool calls all
  arrive numbered 0, each with its own id, which merged two calls into one.
  `_call_index` now starts a new call when a new id appears.

### New Python ideas

- **Subclass and override the few methods that differ**: the stream parser,
  retries and tool-call handling stay in one place (`openai_compat.py`).
- **Read the other side's source code** before writing a client for it: the
  comments in `openwebui.py` cite what Open WebUI does, so a later reader can
  check it again when Open WebUI changes.

### Try it

```
cmcoder login --provider <name>       # your Open WebUI provider's key (Settings → Account)
cmcoder doctor                        # which backend, window, tool calling
```

## 2. Carry-overs: the VS Code panel's commands

*Code:* `cli/stdio.py` (`_panel_command`, `_rewind`), the protocol pair
`rewind_points` → `rewind` → `rewound` + `history`. *Tests:*
`tests/test_panel_commands.py`, `vscode/test/webview.test.ts`.

`/rewind`, `/model`, `/cost`, `/todos` and `/help` now work in the VS Code
panel. A panel command is answered by the stdio server itself
(`PANEL_COMMANDS`), never sent to the model: the server turns it into ordinary
events (an `assistant_message`, a `todo_update`, ...) so the panel needs no
special drawing code. `/rewind` uses VS Code's pick lists (which message, then
code / conversation / both). Found on the way, also in the terminal: rewinding
the conversation kept the todo list from after that point; it's restored now.

Decisions recorded with this item: the classic terminal UI stays the default
(the TUI is `--tui`), and building the extension needs Node.js 22 (or use the
`.vsix` from CI).

## 3. The Bash sandbox

*Code:* `src/cmcoder/sandbox/` (`__init__.py`: `detect`, `Sandbox`,
`seatbelt_profile`, `make_sandbox`; `proxy.py`: `FilteringProxy`;
`bridge.py`), `tools/shell.py` (`PersistentShell(sandbox=...)`),
`tools/bash.py` (`dangerously_disable_sandbox`), `core/permissions.py`.
*Tests:* `tests/test_sandbox.py` (real bubblewrap on Linux CI, `sandbox-exec`
on macOS; the evals run sandboxed there too).

### The problem

Permission prompts protect only as well as the person answering them. After
the hundredth "Allow `pytest`?", people click yes without reading. A sandbox
makes the shell *unable* to do harm outside the project, so harmless commands
can run without asking.

### The idea

Run the agent's persistent shell inside an operating-system sandbox:

| Platform | How |
|---|---|
| Linux, WSL2 | bubblewrap (`bwrap`): a new mount, PID and network namespace |
| macOS | `sandbox-exec` with a generated profile |
| Native Windows | none: permission prompts stay the protection |

Inside: `/` read-only, the project and a temp folder writable, credentials
hidden (`~/.ssh`, `~/.aws`, ...), and **no network except through cmcoder's
filtering proxy**, which allows only `sandbox.network.allowedHosts`.

### The code

- **`detect()`** finds out *once* whether a sandbox works here (it actually
  starts one with `_try`, because "bwrap is installed" doesn't mean
  unprivileged namespaces are allowed), and says why not, with the fix.
- **`Sandbox._bwrap`** builds the command line: `--ro-bind / /`, a private
  `--tmpfs /tmp`, `--bind` for the writable folders, `--ro-bind` for
  `.git`, `.cmcoder`, `.vscode`, `.idea`, `.mcp.json` (made as empty
  placeholders first, so a missing one can't be created), `--tmpfs` over
  `/run` and other sockets, `--unshare-net`. The shell then starts a
  **bridge** inside that forwards `127.0.0.1:3128` to the proxy's Unix socket,
  the only way out.
- **`seatbelt_profile`** writes the macOS rules (S-expressions) with the same
  folders, and allows only the proxy's localhost port.
- **`FilteringProxy`** is an `asyncio` HTTP proxy: for `CONNECT host:port`
  (HTTPS) it checks the host against the allowlist, then pipes bytes both
  ways; for plain HTTP it rebuilds the request from what it checked. A refused
  host gets a 403 that names the setting to change. It chains to the user's
  own `HTTPS_PROXY`.
- **`PersistentShell`** runs *inside* the sandbox, so `cd` and variables still
  carry over between commands. Package caches (pip, npm, ...) point into the
  sandbox's temp folder, so a sandboxed command can't poison caches used
  later outside it. Variables that point at sockets outside (`SSH_AUTH_SOCK`,
  `DOCKER_HOST`, ...) and cmcoder's API key are removed from its environment.
- **Permissions** (`core/permissions.py`): a command that runs in the sandbox
  is allowed without a prompt (deny rules and high-risk commands unchanged).
  `dangerously_disable_sandbox` (the model asks to run outside, after the
  sandbox blocked something) asks *every time*, even over an allow rule;
  `allowUnsandboxedCommands: false` forbids it.

### New Python ideas

- **`asyncio.start_server` / `start_unix_server`**: a network server in a few
  lines; each connection is a coroutine with a `StreamReader` and
  `StreamWriter`.
- **Building `argv` lists, never shell strings**, for every program cmcoder
  starts: no quoting bugs, no injection.
- **Detect by trying**: `_try()` runs the real thing with a timeout, rather
  than guessing from what's installed.

### Try it

```
cmcoder doctor                       # "Bash sandbox: bubblewrap (Linux)" or why not
cmcoder -p "run: curl -sI https://example.com"   # 403 from the proxy unless allowed
```

## 4. OpenTelemetry (off by default)

*Code:* `src/cmcoder/telemetry.py` (`Telemetry`, `from_settings`), settings
`telemetry`, events fed from `Agent.run`. *Tests:* `tests/test_telemetry.py`
(a stand-in collector; prompts and file names never appear in what's sent).

### The problem

A company that rolls out cmcoder wants to know: how many people use it, how
many tokens per model, which tools fail. It must never learn *what* people
asked or which code they work on.

### The idea

Count, don't record. Eight counters (sessions, tokens, cost, tool calls by
result, API errors, compactions, turns, turn time), each with a few labels
(model, tool name, result), sent to the company's OpenTelemetry collector as
OTLP/HTTP JSON every 60 seconds and when a session ends.

### The code

- **`Telemetry.sums`** is a dict keyed by `(metric name, attributes)`, where
  the attributes are a *sorted tuple of pairs*, so equal labels land on the
  same key. `add()` increments.
- **`observe(event, model, session_id)`** turns agent events into counts: a
  `tool_result` adds one to `cmcoder.tool.calls{tool, result}`, a `usage`
  event adds tokens, and so on. MCP tools are counted per server
  (`tool_label`: `mcp__github`), since one server can have many tools.
- **`payload()`** writes OTLP's JSON shape by hand (resource, scope, metrics,
  cumulative "sum" points). No OpenTelemetry SDK: it would add about ten
  packages for a handful of counters. Checked against a real Collector.
- A collector that can't be reached never disturbs the session; `doctor`
  sends an empty export to check it.
- A repository's `telemetry` settings need trust, or a repository could send
  your usage to its own collector.

### New Python ideas

- **Tuples as dictionary keys** (hashable, so `(name, (("model", "x"),))`
  works), and `time.time_ns()` for the timestamps OTLP wants.

## 5. Standalone build and per-platform VS Code extension

*Code:* `packaging/build.py` (PyInstaller), `packaging/cmcoder_entry.py`,
`packaging/vsix.py`, `.github/workflows/release.yml`,
`vscode/src/executable.ts`. *Tests:* `tests/test_standalone.py` run against
each platform's build.

- **PyInstaller in folder mode** (`dist/cmcoder/`: the program and its
  `_internal` files), not one file: it starts fast and isn't unpacked into a
  temp folder on every run (which antivirus software dislikes).
- **No separate Python in the build**, so the Linux sandbox's bridge runs as
  `cmcoder --sandbox-bridge` (`sandbox.bridge_command`).
- **`vsix.py`** copies the build into the extension's `bin/cmcoder/` and
  runs `vsce package --target win32-x64|linux-x64|darwin-arm64`; the
  extension uses the bundled program unless `cmcoder.executable` is set.
- The test suite runs the *built* program as users would: version, doctor, a
  `-p` turn, the VS Code protocol, the sandbox bridge.

## 6. Branding: name and icon

*Code:* `packaging/brand.py` (checks and generated files), `branding/`,
`src/cmcoder/brand.py` (read at run time), `cli/terminal_profile.py`. *Guide:*
[branding.md](branding.md).

- One folder, `branding/`, with `icon.png`, `icon-mono.svg`, `brand.json` and
  `logo.txt`. **Checked at build time** (`brand.py`: the PNG opened with
  Pillow for its size, squareness and transparency, a one-colour SVG parsed
  without DTDs, text limits), which also makes `cmcoder.ico` and the Windows
  file details.
- At run time, `brand.load()` reads the bundled copy (or the repository's
  folder in development) and falls back to cmcoder's own name, so a broken
  brand never stops the program.
- `cmcoder terminal-profile` writes a Windows Terminal *fragment* (a JSON
  file in its own folder), so Windows Terminal's own settings are never
  edited.

## 7. Tests, evals and the security review

- Evals through the Open WebUI mock: `evals/run.py --mock --gateway
  openwebui-ollama|openwebui-openai` (23/23 on both, through `-p` and the
  VS Code protocol).
- [SECURITY-REVIEW.md](SECURITY-REVIEW.md): a sandbox-escape review found 6
  issues (local services' sockets, `.git`, missing protected folders, macOS
  local ports, the proxy's request text, the API key in the shell's
  environment), each fixed with a test that fails on the earlier code.
