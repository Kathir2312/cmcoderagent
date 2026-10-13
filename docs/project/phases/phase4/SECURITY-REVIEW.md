# Phase 4 security review: sandbox, SAST and SCA

**Date:** 4 October 2026. **Scope:** the whole repository as of Phase 4
item 6, with the closest look at what Phase 4 added: the Bash sandbox
(`sandbox/`), its filtering proxy and network bridge, Open WebUI as a gateway
(`providers/openwebui.py`), OpenTelemetry metrics (`telemetry.py`), the
standalone build and per-platform VS Code extension (`packaging/`), and
branding (`brand.py`, `packaging/brand.py`, `cmcoder terminal-profile`).

**Result:** 6 issues fixed (2 high, 2 medium, 2 low), all in the sandbox
and its proxy except one in the branding build step. No known-vulnerable
dependency. Everything the scanners still report was traced to the code and
is a false positive or accepted with a reason (below). Each fix has a test
that fails on the code before it.

## How it was tested

| Kind | Tool | Version | What it covered |
|---|---|---|---|
| SAST (Python) | Bandit | 1.9.4 | `src/`, `evals/run.py`, `packaging/` (all rules) |
| SAST (Python, TypeScript, JavaScript, secrets) | Semgrep | 1.179.0, rules from `semgrep/semgrep-rules` commit `a84ff9c` (22 Sep 2026): `python`, `javascript`, `typescript`, `generic/secrets`, `ai`, `bash` | 169 files: `src`, `vscode/src`, `vscode/test`, `evals`, `packaging`, `tests`, `branding` |
| SCA (Python) | pip-audit | 2.10.1 | all 68 packages pinned in `uv.lock` (runtime + dev, Pillow included) |
| SCA (npm) | npm audit | npm 10.9.4 | the extension's packages (`package-lock.json`) |
| Sandbox review | manual, with tests against real bubblewrap (Linux) and sandbox-exec (macOS CI) | bubblewrap 0.9.0 | what a sandboxed command can still read, write, connect to or start; the proxy's request handling |

As in Phases 2 and 3, Semgrep's registry is blocked by this environment's
network policy, so the same public rules were cloned and run locally.

## Findings and fixes

| # | Severity | Finding | Status |
|---|---|---|---|
| 1 | **High** | Linux: services outside the sandbox were reachable through their Unix sockets | Fixed |
| 2 | **High** | Git's own folder could be changed from inside the sandbox in ways that run code outside it later | Fixed (decided 4 Oct: all of `.git` read-only) |
| 3 | Medium | Protected folders that didn't exist yet (`.vscode`, `.mcp.json`, ...) could be created from inside the sandbox | Fixed |
| 4 | Medium | macOS: sandboxed commands could connect to any local port, and ask the system to start programs | Fixed |
| 5 | Low | The proxy forwarded the client's own request text, not one rebuilt from what it checked | Fixed |
| 6 | Low | cmcoder's API key was in the sandboxed shell's environment; the branding SVG was parsed with DTDs allowed | Fixed |

### 1. Local services through Unix sockets (Linux)

The sandbox has its own network namespace, which blocks network connections
but not Unix sockets on the file system, and `/` is visible read-only (a
read-only socket file can still be connected to). Services that listen on
such sockets act for the user outside the sandbox: the desktop session's
message bus, Docker, and on WSL2 the bridge that starts Windows programs.

**Fix:** `/run`, `/var/run`, the user's runtime folder (`XDG_RUNTIME_DIR`)
and WSLg's folder are replaced by empty ones inside the sandbox; `~/.docker`
is hidden; sockets named in `SSH_AUTH_SOCK`, `DBUS_SESSION_BUS_ADDRESS`,
`DOCKER_HOST` and `WSL_INTEROP` are covered wherever they are, and those
variables are removed from the sandboxed shell. `/tmp` was already private.
Test: `test_local_services_are_out_of_reach` (a socket outside the sandbox
can't be reached).

### 2. Git's folder (decided: all of `.git` read-only)

Only `.git/hooks` and `.git/config` were read-only. Git reads its settings
and hooks from more places in `.git` than those two, and some settings make
git run a program; that program would run outside the sandbox the next time
the user or VS Code runs git. Protecting a list of paths can't be complete,
so (decision of 4 October) **all of `.git` is read-only** in the sandbox.
Git commands that only read (`status`, `diff`, `log`) work there; ones that
write (`commit`, `checkout`, `stash`, `init`) fail in the sandbox and are
run outside it with the user's approval (the model is told so in its
prompt). Tests: `test_git_reads_work_and_writes_dont`.

### 3. Protected folders that didn't exist yet

bubblewrap can only mount over something that exists, so a protected folder
the project didn't have (`.vscode`, `.mcp.json`, `.git`) could be created
from inside the sandbox. **Fix:** on Linux, missing ones are created as empty
read-only placeholders when the sandbox starts and removed when the session
ends (only if still empty). Git ignores an empty `.git` folder; cmcoder
treats a `.mcp.json` folder as no file. `.idea` (JetBrains) is protected too
when present. macOS rules are by path, so they already covered missing
paths. Test: `test_missing_protected_folders_cant_be_created`.

### 4. macOS: local ports and starting programs

The macOS profile allowed connections to any port on localhost, which
reaches local services outside the sandbox (for instance a local proxy
that connects anywhere). **Fix:** connections go only to cmcoder's proxy
port; `sandbox.network.allowLocalhost: true` opens all local ports for those
who need it (e.g. a test server the command starts itself). The profile also
denies creating launchd jobs and sending Apple events, so programs can't be
started outside the sandbox that way. Test: `test_direct_local_connections_are_blocked`
(macOS CI; on Linux the sandbox has its own localhost).

### 5. The proxy's plain-HTTP requests

For `http://` requests the proxy checked the host, then forwarded the
client's request line, headers and everything that followed on the
connection. Through the user's own proxy, a second request on the same
connection, or a URL that two parsers read differently, could reach a host
that wasn't checked. **Fix:** exactly one request is forwarded, rebuilt from
what was checked (URL and `Host` header), with exactly its declared body;
chunked uploads are refused (`https://` goes through `CONNECT`, unchanged).
Test: `test_the_proxy_forwards_one_checked_request`.

### 6. Smaller fixes

- The sandboxed shell no longer gets `CMCODER_API_KEY` (or `OPENAI_API_KEY`
  when that is cmcoder's key).
- `packaging/brand.py` rejects `<!DOCTYPE>` and `<!ENTITY>` in the
  side-bar SVG before parsing it (Semgrep `use-defused-xml`). It's the
  company's own file at build time, but an SVG from elsewhere has no need
  for them. Test: `test_the_side_bar_icon_is_one_colour`.

## Reviewed and unchanged

| Area | What was checked |
|---|---|
| Open WebUI provider | The API key goes only to the configured server, like LiteLLM's; Open WebUI's `{"detail": ...}` errors and extra stream data are shown as text, never run; `/ollama/api/show` is read-only |
| OpenTelemetry | Off by default; counts and timings only (tested: a prompt's text, file names and commands never appear in an export); a repository can't set the endpoint without trust; headers expand `${VAR}` from the user's environment only |
| Standalone build and VSIX | The extension uses the bundled cmcoder only when `cmcoder.executable` isn't set, and a workspace can't set that in an untrusted workspace (Phase 2); vsce's secret scan runs on the bundle; links are copied as files |
| Branding | `brand.json` text is checked (printable, length) and shown escaped in the terminal and in the panel's HTML; `logo.txt` can't hold escape codes; the VS Code panel's CSP is unchanged |
| `cmcoder terminal-profile` | Writes only its own fragment file under `%LOCALAPPDATA%`; never Windows Terminal's settings |

## Accepted risks

- **Allowed hosts are trusted as a whole.** Once `pypi.org` is allowed, a
  command can send data to it; and a host that serves many sites (a CDN) can
  be reached with another site's name inside the encrypted connection. Keep
  `allowedHosts` short.
- **New git repositories in subfolders.** A sandboxed command can create a
  git repository in a subfolder of the project. VS Code may open such
  repositories by itself (`git.autoRepositoryDetection`), and the user may
  run git there. Review unexpected nested repositories before using them.
- **What the sandbox can read.** Everything the user can read except the
  hidden credential folders and `sandbox.denyReadPaths`; secrets elsewhere
  (e.g. a token in a shell profile) are readable, though the network allows
  only the listed hosts.
- **Project files are prompt text.** A sandboxed command can change
  `CMCODER.md`, commands and skills in the project (outside `.cmcoder`), which
  the model reads later; what the model then does still goes through the
  permission engine.
- **macOS services.** sandbox-exec's profile starts from "allow" and denies
  writes, network, job creation and Apple events; other system services stay
  reachable.
- **Native Windows** has no sandbox (decided in the plan); permission
  prompts remain the protection, and WSL2 gets the Linux sandbox.

## Scanner results, reviewed

| Tool | Result |
|---|---|
| Bandit | 0 high, 0 medium, 73 low (categories below) |
| Semgrep | 101 findings, reviewed below |
| pip-audit | No known vulnerabilities (68 packages) |
| npm audit | 0 vulnerabilities |

As in [Phase 3](../phase3/SECURITY-REVIEW.md#scanner-results-reviewed)
(same rules, same reasons): Bandit B101/B110/B311/B404/B603/B607, Semgrep
`dangerous-subprocess-use-audit`, `dangerous-asyncio-create-exec-audit`,
`is-function-without-parentheses`, `return-not-in-function`,
`useless-inner-function`, `open-never-closed`,
`hooks-unconditional-allow-generic`, the VS Code panel's
`html-in-template-string` / `jquery-*` / `insufficient-postmessage-origin-validation`,
`tempfile-without-flush`, `arbitrary-sleep`, `detect-generic-ai-*`,
`detect-anthropic`, and eval fixtures. New in Phase 4:

| Finding | Where | Why it's not a problem |
|---|---|---|
| Bandit B603, Semgrep `dangerous-subprocess-use-audit` | `sandbox/__init__.py` (the availability check), `packaging/vsix.py` | Argument lists, no shell: `bwrap`/`sandbox-exec` by full path; `node`/`npx` at packaging time |
| Semgrep `use-defused-xml`, `use-defused-xml-parse` | `packaging/brand.py` | Fixed (finding 6): DTDs rejected before parsing; build-time only |
| Semgrep `html-in-template-string` | `vscode/src/chatView.ts` | The product name in the panel's HTML is escaped (`escapeHtml`); the CSP still allows no inline script |
| Semgrep `mcp-tool-poisoning-generic` | `sensitive.py` | Matches a docstring that names `~/.ssh/id_rsa` as an example of what's refused |

## What runs in CI

The `security` job (Bandit at medium or higher, pip-audit, npm audit at high
or higher) covers the new code. The sandbox tests run against real
bubblewrap (Linux) and sandbox-exec (macOS) in every CI run; the Release
build runs the standalone tests on all three platforms, including the copy
inside the extension. Semgrep stays a manual step while its registry is
unreachable from here.
