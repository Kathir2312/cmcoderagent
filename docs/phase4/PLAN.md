# Phase 4 — Hardening: plan

**Goal** ([DESIGN.md §16](../DESIGN.md#16-roadmap)): a release candidate other
developers can use. **Done when:** they install one thing, it works with the
LiteLLM gateway or Open WebUI, the agent's shell commands can't do harm
outside the project (where a sandbox is available), and the company can see
usage if it wants to.

## Decisions (3 October 2026)

| Question | Decision |
|---|---|
| SSO login (Okta/OIDC) | **Removed** from the roadmap. API keys only; the auth-provider interface stays. |
| OpenAI Responses API / Anthropic API adapters | **Dropped.** LiteLLM and Open WebUI both speak the chat-completions format. |
| Gateways | LiteLLM (as before) **plus Open WebUI**. What runs behind Open WebUI is unknown, so both Ollama and OpenAI-compatible backends are handled; `cmcoder doctor` tells which. |
| Bash sandbox | **Linux, macOS, and Windows through WSL2.** Native Windows keeps permission prompts as the protection (as Claude Code does). |
| OpenTelemetry | **Yes, off by default.** Counts and timings only, never prompts, code, paths or commands. |
| TUI default (3 Oct, item 2) | **Classic stays the default**; the full-screen UI remains opt-in (`--tui`). |
| Sandbox: prompts (3 Oct, item 3) | **Auto-allow**: a command that runs in the sandbox doesn't ask (deny rules and high-risk commands unchanged). |
| Sandbox: network (3 Oct, item 3) | **Blocked except an allowlist** of hosts in settings. |
| Sandbox: `.git` (4 Oct, security review) | **All of `.git` read-only** in the sandbox. Git commands that write run outside it, with the user's approval. |
| Branding (4 Oct, added as item 6) | **Icon and name** from a `branding/` folder, replaced before building; **Windows Terminal profile** included. The company logo is dropped in by the team before they build the VS Code extension. |

## Items, in order

### 1. Open WebUI gateway

- Provider `"type": "openwebui"`: the server's URL; cmcoder uses Open WebUI's
  OpenAI-compatible `/api/chat/completions` and `/api/models`.
- Auth: an Open WebUI API key (Settings → Account; an admin enables API
  keys), kept like the LiteLLM key (keychain or environment, never a file).
- Models from `/api/models`, without entries that aren't chat models.
- Context window: no `/model/info` there, so from what `/api/models` reports
  (Ollama models' `num_ctx`), what cmcoder learns from "too long" errors, and
  a `doctor` probe; a warning for Ollama's small default window and which
  Open WebUI setting raises it.
- Tool calling depends on each model's "function calling" setting: `doctor`
  checks native tool calls get through; otherwise the prompted-tool fallback
  (Phase 1) and the setting to change.
- Open WebUI's extra stream data (sources, status) is ignored; errors inside
  a stream are reported clearly.
- Tests: an Open WebUI mode for the mock server; a CI job with a real Open
  WebUI in front of the mock model if it fits CI (else a manual run). A setup
  guide.

**Status: done.** `src/cmcoder/providers/openwebui.py` (`OpenWebUIProvider`),
Ollama options in `openai_compat.build_request` (profile `backend: "ollama"`),
`factory._ollama_window`, doctor lines; guide [openwebui.md](openwebui.md).
- Read from Open WebUI's source (0.11.4) and then checked against a real Open
  WebUI with both backends: `/api/models` hides a model's settings, so its
  `num_ctx` can't be read; a request's `options` win over them, so cmcoder
  sends `num_ctx` itself (capped at the trained length from `/ollama/api/show`)
  and doesn't run the context probe (Ollama never refuses a long prompt).
- Found and fixed: Ollama's tool calls come through Open WebUI all numbered 0
  (each with its own id), which merged two calls into one; a new id now starts
  a new call.
- `think: false` is sent only for models known to think (Ollama rejects it
  for others); `num_ctx` only for Ollama models (Open WebUI would pass it on to
  other backends as an unknown field).
- Tests: `tests/test_openwebui.py` (mock server's Open WebUI mode, 9 tests);
  `tests/test_openwebui_real.py` with a real Open WebUI in front of the mock
  model and a stand-in Ollama (`tests/fake_ollama.py`), in its own CI workflow
  (`.github/workflows/openwebui.yml`: by hand, weekly, and when the provider
  code changes), because Open WebUI is a ~7 GB install.

### 2. Carry-overs

- VS Code panel: `/rewind`, `/cost`, `/model`, `/todos`.
- Decide whether the full-screen TUI becomes the default.
- Node.js 22 for building the extension.

**Status: VS Code commands done** (the TUI default and Node 22 are decisions /
setup for you, below). `/rewind`, `/model [name]`, `/cost`, `/todos` and `/help`
work in the panel and are offered in completion (`cli/stdio.py`
`_panel_command`). `/rewind` asks with VS Code's pick lists (which message,
then code / conversation / both, then files outside the project), new protocol
pair `rewind_points` → `rewind` → `rewound` + `history`; the panel redraws the
conversation and puts the message back in the input. Found on the way (also in
the terminal): rewinding the conversation kept the todo list from after that
point; it is now restored as it was. Tests: `tests/test_panel_commands.py`,
`vscode/test/webview.test.ts`.

### 3. Bash sandbox

- Linux: bubblewrap; macOS: `sandbox-exec`; Windows: inside WSL2 (the Linux
  path). Writes only in the project and temp folders; network only to allowed
  hosts.
- On where available; `doctor` and the permission prompt say whether a
  command runs sandboxed. Native Windows unchanged.
- Decided when the item starts: may sandboxed commands skip the permission
  prompt (Claude Code's auto-allow)?

**Status: done.** `src/cmcoder/sandbox/` (`Sandbox`, `detect`,
`seatbelt_profile`; `proxy.py` the filtering proxy; `bridge.py` inside the
Linux sandbox), the sandboxed `PersistentShell`, Bash's
`dangerously_disable_sandbox`, the policy rules, `doctor` section, a note in
the system prompt. Tests: `tests/test_sandbox.py` (real bubblewrap in CI on
Linux, `sandbox-exec` on macOS; the evals run sandboxed there too).
- The persistent shell runs inside the sandbox, so `cd` and variables still
  carry over. bubblewrap: read-only `/`, private `/tmp`, the project and a temp
  folder writable, own PID and network namespaces (`--die-with-parent`,
  `--new-session`); a timeout kills everything inside.
- Read-only inside the project: `.git` (all of it, decided 4 Oct after the
  security review: git's settings and hooks would run outside the sandbox
  later; git commands that write run outside it, with approval), `.cmcoder`
  (cmcoder's permissions), `.vscode`, `.idea`, `.mcp.json`; on Linux, empty
  placeholders while the session runs, so missing ones can't be created.
  Local services' sockets (`/run`, the runtime folder, Docker) are hidden. Hidden: `~/.ssh`, `~/.aws`, `~/.azure`,
  gcloud, `~/.kube`, Docker's config, `~/.netrc`, git credentials, `~/.gnupg`,
  `~/.pypirc`, cmcoder's credentials file, plus `sandbox.denyReadPaths`.
- Package caches (pip, npm, yarn, Go, XDG) go to the sandbox's temp folder, so
  a sandboxed command can't poison caches used unsandboxed later.
- Network: Linux has none except a bridge to the proxy's Unix socket; macOS
  only localhost (the proxy, and local dev servers). The proxy allows
  `sandbox.network.allowedHosts` (`*.example.com` for subdomains), chains to
  the user's own HTTPS_PROXY, and answers others with a 403 naming the setting.
- Leaving the sandbox (`dangerously_disable_sandbox`) asks every time, even
  over an allow rule ("always allow" isn't offered); `allowUnsandboxedCommands:
  false` (e.g. managed) forbids it; plan mode denies it.
- `CMCODER_SANDBOX=off|on|auto`. A repository's `sandbox` settings need trust.

### 4. OpenTelemetry (off by default)

- OTLP export configured in settings or managed settings (standard `OTEL_*`
  variables too): sessions, tokens and cost per model, tool calls by name and
  permission decision, errors, latency.

**Status: done.** `src/cmcoder/telemetry.py`, settings `telemetry`, recorded
from the agent's events (`Agent.run`), shared with subagents, exported every
60 s and at the end of a session; `doctor` checks the collector (an empty
export, no fake data). Tests: `tests/test_telemetry.py` (a stand-in
collector; the prompt and file names never appear in what is sent).
- No new packages: OTLP/HTTP with JSON encoding, cumulative sums, written
  directly (the official SDK would add ~10 packages for a handful of
  counters). Checked against a real OpenTelemetry Collector (0.115), which
  decoded every metric.
- Recorded: sessions by front end, tokens and cost per model, tool calls by
  tool and result (ok/error/denied; MCP tools grouped by server), model
  errors by kind, compactions, turns and total turn time. Each point carries
  the session id; the resource a random installation id plus your
  `resourceAttributes` (`${VAR}` expanded, e.g. a user name if the company
  wants it).
- A repository's `telemetry` settings need trust (it could otherwise send
  your usage to its own collector).

### 5. Standalone binary and per-platform VS Code extension

- PyInstaller builds for Windows x64, Linux x64, macOS arm64; each `.vsix`
  carries its `cmcoder` (PATH as fallback), so users need no Python or uv.
- Built in CI as downloads; code signing only with a company certificate.

**Status: done.** `packaging/build.py` (PyInstaller, a program folder, ~55 MB:
starts fast and isn't unpacked to a temp folder on every run, which antivirus
software also dislikes), `packaging/vsix.py` (the extension with it in
`bin/cmcoder/`, `vsce package --target`), workflow
`.github/workflows/release.yml` (Windows x64, Linux x64, macOS arm64; on code
changes and on demand). The extension (`vscode/src/executable.ts`) uses the
bundled program unless `cmcoder.executable` is set, for the chat and for Open in
Terminal.
- In the standalone build there's no separate Python, so the Linux sandbox's
  bridge runs as `cmcoder --sandbox-bridge` (`sandbox.bridge_command`).
- Tested as users run it: `tests/test_standalone.py` (version, doctor, a
  `-p` turn with Read and Bash, the VS Code protocol, the sandbox bridge) on
  each platform's build; on Linux the extension runs in a real VS Code with
  the bundled program (`CMCODER_TEST_BUNDLED`). Executable bits survive in the
  `.vsix` (checked).
- Not done: code signing (needs a company certificate; unsigned programs may
  get a SmartScreen / Gatekeeper warning the first time).

### 6. Branding: icon and name (added 4 October)

- One folder, `branding/`: `icon.png` (full colour), `icon-mono.svg` (VS
  Code's side bar), `brand.json` (product name, VS Code publisher, company,
  copyright, accent colour), `logo.txt` (text logo for the terminal).
  Replace the files, then build; nothing else to edit.
- Checked at build time (`packaging/brand.py`: sizes, square, transparency,
  one-colour SVG, text limits), which also makes `cmcoder.ico`, the 256 px
  PNG and the Windows file details.
- VS Code: Extensions list icon, side bar icon, name and publisher (in the
  packaged copy only), the chat panel's empty screen and reply avatar, the
  terminal tab, and messages that name the product.
- CLI: name, text logo and accent colour at startup (classic and TUI), the
  terminal window title; `cmcoder.exe` gets the icon and file details.
- `cmcoder terminal-profile`: a Windows Terminal profile with the name and
  icon (a fragment; `--remove`, `--print`).
- The `cmcoder` command, settings and command IDs keep their names.
- Status (4 Oct): done. Tests for the checks and outputs, the package.json
  changes, the Windows Terminal profile, the webview; the standalone build
  carries the branding, and on Windows the exe's icon and ProductName are
  read back. Guide: [branding.md](branding.md).

### 7. Tests, evals and security review

- Evals through the Open WebUI mock; sandbox tests that try to escape.
- SAST/SCA as in Phases 2–3, plus a sandbox escape review.

### 8. Guides and docs

- Guide sections per item, the Open WebUI setup page; hands-on checklist on
  Windows with LiteLLM and Open WebUI; `STATUS.md`.

## Not in Phase 4

SSO, other API formats (decided above). Other IDEs, output styles and a
status line, plugins and marketplaces, image/PDF/notebook reading, mTLS
client certificates: later, if needed.

## Checklist

- [x] 1. Open WebUI gateway
- [x] 2. Carry-overs (VS Code commands, TUI default, Node 22)
- [x] 3. Bash sandbox
- [x] 4. OpenTelemetry
- [x] 5. Standalone binary and per-platform VSIX
- [x] 6. Branding: icon and name
- [ ] 7. Tests, evals and security review
- [ ] 8. Guides and docs
- [ ] Hands-on use on Windows (LiteLLM and Open WebUI), and `STATUS.md`
