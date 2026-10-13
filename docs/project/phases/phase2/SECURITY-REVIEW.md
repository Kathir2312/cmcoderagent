# Phase 2 security review: SAST and SCA

**Date:** 3 October 2026. **Scope:** the whole repository as of Phase 2: the
Python engine (`src/cmcoder`), the VS Code extension (`vscode/src`), the eval
runner (`evals/run.py`) and both dependency trees. Phase 2 code got the
closest look, together with older code it exposes (the extension starts
`cmcoder` in whatever folder you open).

**Result:** 7 issues fixed (3 high, 2 medium, 2 low), plus one dev-only
dependency issue. No known-vulnerable dependency remains. Everything still
reported by the scanners was reviewed and is either a false positive or
accepted with a reason (below).

## How it was tested

| Kind | Tool | Version | What it covered |
|---|---|---|---|
| SAST (Python) | Bandit | 1.9.4 | `src/`, `evals/run.py` (all rules) |
| SAST (Python, TypeScript, JavaScript, secrets) | Semgrep | 1.179.0, rules from `semgrep/semgrep-rules` commit `a84ff9c` (22 Sep 2026): `python`, `javascript`, `typescript`, `generic/secrets`, `ai`, `bash` | 892 rules on 58 files, tests included |
| SCA (Python) | pip-audit | 2.10.1 | all 47 packages pinned in `uv.lock` (runtime + dev) |
| SCA (npm) | npm audit | npm 10.9.4 | all extension dependencies (`package-lock.json`) |
| Manual | review + proof of concept | | every scanner finding traced to the code; trust boundaries (repository files, workspace settings, model output, the webview) checked by hand |

Semgrep's registry (`semgrep.dev`) is blocked by this environment's network
policy, so the same public rules were cloned from GitHub and run locally.

## Findings and fixes

| # | Severity | Finding | Found by | Status |
|---|---|---|---|---|
| 1 | **High** | A repository's settings could run code without approval (`env: {"BASH_ENV": ...}`) | manual, proof of concept | Fixed |
| 2 | **High** | A repository's settings could redirect the gateway, sending your API key to it | manual | Fixed |
| 3 | **High** (Windows) | Programs started by bare name could come from the project folder (`git.exe`, `rg.exe`, `bash.exe`, `taskkill.exe`, and the extension's `cmcoder.exe`) | Bandit B607, then manual | Fixed |
| 4 | Medium | A repository's settings could add allow rules or switch on `bypassPermissions` / `acceptEdits` | manual | Fixed |
| 5 | Medium | A workspace's `.vscode/settings.json` could set `cmcoder.permissionMode` to `bypassPermissions` | manual | Fixed |
| 6 | Low | Editor context could send a protected file's contents (`.env`, keys) to the model | manual | Fixed |
| 7 | Low | Model Markdown written with `innerHTML` in the chat panel | Semgrep `insecure-innerhtml` | Hardened |
| 8 | Low | IDE tools `openFile` / `getDiagnostics` skipped the path checks Read has | manual | Fixed |
| 9 | Low (dev only) | `braces` ≤ 3.0.3 denial of service (GHSA-vfj7-8cjw-p6xm), through the `.vsix` packaging tool | npm audit (6 "high" entries, one root) | Fixed |
| 10 | Info | Editor text could close the `<system-reminder>` note or its code fence | manual | Hardened |
| 11 | Info | A session id from the webview became a command-line argument unchecked | manual | Hardened |

### 1, 2, 4. Settings a repository writes for itself

`.cmcoder/settings.json` (and `settings.local.json`, which a repository can
commit too) were merged into cmcoder's settings like your own. Proof of
concept (now `tests/test_project_trust.py::test_bash_env_poc_no_longer_runs`):
a repository with `{"env": {"BASH_ENV": "evil.sh"}}` got `evil.sh` run by the
first auto-approved `ls`. The same files could set `providers` (your API key
sent to their URL), allow rules and permissive modes.

**Fix** (`config/settings.py`, "Project trust"):

- `providers` is **never** read from a project, trusted or not.
- `env`, `permissions.allow` and the `acceptEdits` / `bypassPermissions`
  modes are used only for a **trusted** project: `cmcoder trust` (lists what
  it enables; `--revoke` undoes it), `--trust-project` for one run, or the
  VS Code extension for a workspace VS Code itself trusts.
- "Always allow" answers cmcoder writes to `settings.local.json` stay trusted
  through a fingerprint, as long as nobody else changes the file; rules a
  repository committed there never become trusted by this.
- Every front end (REPL, TUI, `-p`, the extension) and `cmcoder doctor` say
  what was ignored and how to trust the project.

### 3. Programs found in the current folder (Windows)

On Windows, starting a program by bare name (`subprocess.run(["git", ...])`)
and `shutil.which` both look in the current folder first; Node's `spawn`
looks in the child's working folder. cmcoder runs in your project, so a cloned
repository with `git.exe` at its root ran it at startup (git info for the
prompt), `rg.exe` on the first Grep, `bash.exe` on the first command, and the
extension could start a planted `cmcoder.exe`.

**Fix:** `compat.find_program()` (Python) and `resolveExecutable()`
(extension) search only absolute `PATH` entries, never the current or
workspace folder, and the program is then started by its full path.
`taskkill` is run from `System32` by full path. Tests in
`tests/test_compat.py` and `vscode/test/agentProcess.test.ts`.

### 5. A workspace choosing the permission mode

`cmcoder.permissionMode` could be set by the workspace, including in
untrusted ("Restricted Mode") workspaces. **Fix:** the setting is
user-settings only (`"scope": "machine"`) and restricted in untrusted
workspaces, like `cmcoder.executable` and `cmcoder.executableArgs` already
were.

### 6. Protected files in the editor context

Read refuses `.env`, `*.pem` and files under deny rules, but a selection in
such a file, or its problems, went to the model with each message. **Fix:**
the agent asks the permission engine whether Read would be denied; if so, the
note says the file is protected and leaves out its text and problems
(`tests/test_ide.py`, `tests/test_stdio.py`).

### 7. Markdown in the chat panel

Model output was rendered with `marked` (raw HTML escaped) and written with
`innerHTML`, under a strict Content Security Policy (only the bundled script
runs, nothing is loaded from the network). This was safe, but the Markdown
renderer was the only barrier besides the CSP. **Hardened:** the HTML is now
parsed into an inert document, filtered against an allowlist of elements and
attributes (links only `http(s)`, `mailto` or `#`), then inserted. Test:
`vscode/test/webview.test.ts` ("model output can't inject markup").

### 8. IDE tools and paths

`openFile` and `getDiagnostics` had no permission target, so any path was
allowed. **Fix:** like Read, paths inside the project are allowed, outside
ones ask, and deny rules apply.

### 9. Dependency: `braces` (dev only)

`npm audit` reported 6 high entries, all from one root:
`@vscode/vsce` → `secretlint` → `globby` → `fast-glob` → `micromatch` →
`braces` ≤ 3.0.3, for which no fixed release exists. It is the tool that
packages the `.vsix`, not shipped in it. **Fix:** `@vscode/vsce` 4.0.0, which
no longer uses that chain. `npm audit`: **0 vulnerabilities**.

### 10, 11. Hardening

- Editor text inside the note can't close `<system-reminder>` (the tag is
  defused) or the code fence (the fence is longer than any run of backticks
  in the text).
- The extension accepts only a plain session id from the panel and passes it
  as `--resume=<id>`.

## Scanner results after the fixes

| Tool | Result |
|---|---|
| pip-audit | No known vulnerabilities in 47 packages |
| npm audit | 0 vulnerabilities |
| Bandit | 49 findings, **all low severity**, reviewed below; 0 medium or high |
| Semgrep | 51 findings, reviewed below; the `innerHTML` findings are gone |

### Remaining findings: reviewed, no change needed

| Tool / rule | Where | Why it's acceptable |
|---|---|---|
| Bandit B101 `assert_used` (25) | various | Type narrowing for the type checker, never a security check (all 25 read) |
| Bandit B603 / Semgrep `dangerous-subprocess-use-audit` | `compat.py`, `prompt.py`, `evals/run.py` | Argument lists, no shell; programs by full path (finding 3) |
| Bandit B607 (4) | `evals/run.py` | The developer eval runner calling `git` in a folder it created |
| Bandit B110 `try_except_pass` (6) | keychain, TLS, Windows folder lookup | Deliberate fallbacks; each falls back to a safe default |
| Bandit B404 `import subprocess`, B311 `random` | | Import notice; `random` is retry jitter, not security |
| Semgrep `insufficient-postmessage-origin-validation` | `webview/main.ts` | Messages come from VS Code's host; the page has no frames and runs no other script (CSP); unknown message kinds are ignored |
| Semgrep `html-in-template-string`, `prohibit-jquery-html` | `chatView.ts` | The panel's fixed HTML; only a random nonce and VS Code resource URIs are inserted |
| Semgrep `jquery-insecure-method` (6) | `webview/main.ts` | DOM `append(element)` calls, not jQuery, never with HTML strings |
| Semgrep `tempfile-without-flush` | `doctor.py` | The file is closed (and flushed) before it is read |
| Semgrep `detect-generic-ai-*`, `detect-anthropic` | | Informational: the code talks to model APIs |
| Semgrep `is-function-without-parentheses`, `return-not-in-function`, `useless-inner-function`, `arbitrary-sleep`, `python36-*` | | Code-quality rules misreading `Literal` types, decorator-registered functions and test sleeps |
| Semgrep `generic/secrets` | | No findings: no keys or tokens in the code |

## Known risks that remain (by design)

- **Prompt injection.** Text the model reads (files, command output, web
  pages later) can try to steer it. The defences are the permission prompts
  (high-risk commands always ask, even with allow rules), protected files,
  diff review, and not trusting repositories by default. Trusting a project
  or using `bypassPermissions` removes some of these.
- **Trusted projects.** `cmcoder trust` (or a VS Code-trusted workspace)
  gives that repository's `env` and allow rules full effect: trust only
  repositories you would run code from.
- **Data leaves the machine.** Prompts, file contents the model reads, and
  editor context go to your configured gateway (unchanged from Phase 0).

## How to re-run

CI's `security` job runs on every push and fails on a medium/high Bandit
finding, a known-vulnerable Python package, or a high/critical npm advisory:

```bash
uvx bandit -r src evals/run.py --severity-level medium
uv export --format requirements-txt --no-hashes --no-emit-project > requirements.txt
uvx pip-audit -r requirements.txt --no-deps --disable-pip --strict
cd vscode && npm audit --audit-level=high
```

Semgrep (all findings, for review; where `semgrep.dev` is reachable the
`p/python p/typescript p/javascript p/secrets` packs are equivalent):

```bash
git clone --depth 1 https://github.com/semgrep/semgrep-rules /tmp/rules
uvx semgrep scan --metrics=off --config /tmp/rules/python --config /tmp/rules/javascript \
  --config /tmp/rules/typescript --config /tmp/rules/generic/secrets --config /tmp/rules/ai \
  --exclude node_modules --exclude dist src vscode/src evals/run.py
```
