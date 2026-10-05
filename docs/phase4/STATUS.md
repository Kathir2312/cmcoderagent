# Phase 4 — Hardening: status

**Status:** ✅ complete (marked complete on 5 October 2026). **Snapshot:** the
code as of commit `e78bdb1` (the last Phase 4 commit before Phase 5's code);
the guides and this file were added on 5 October.

Phase 4's goal ([PLAN.md](PLAN.md)): *a release candidate other developers
can use: they install one thing, it works with the LiteLLM gateway or Open
WebUI, the agent's shell commands can't do harm outside the project (where a
sandbox is available), and the company can see usage if it wants to.* Met.

## Delivered

All 8 items in [PLAN.md](PLAN.md):

1. **Open WebUI gateway** (`"type": "openwebui"`): Ollama and
   OpenAI-compatible backends, cmcoder's own `num_ctx` (Ollama silently
   drops what doesn't fit), tool calls through Open WebUI fixed, `doctor`
   checks; a real Open WebUI in its own CI workflow. Guide:
   [openwebui.md](openwebui.md).
2. **Carry-overs**: `/rewind`, `/model`, `/cost`, `/todos`, `/help` in the
   VS Code panel; the classic UI stays the default; Node.js 22 for building
   the extension.
3. **Bash sandbox**: bubblewrap on Linux and WSL2, `sandbox-exec` on macOS;
   the project writable, credentials hidden, `.git` and cmcoder's settings
   read-only, network only to allowed hosts through cmcoder's proxy;
   sandboxed commands run without a prompt, leaving the sandbox asks every
   time.
4. **OpenTelemetry** (off by default): eight counters (sessions, tokens,
   cost, tool calls, errors, compactions, turns, turn time) over OTLP/HTTP;
   never prompts, code, paths or commands.
5. **Standalone build and per-platform VS Code extension**: a PyInstaller
   program folder for Windows x64, Linux x64 and macOS arm64, and a `.vsix`
   for each with it inside; no Python or uv needed.
6. **Branding**: the company's name and icon from `branding/`, checked at
   build time, in the CLI, the TUI, VS Code and a Windows Terminal profile.
   Guide: [branding.md](branding.md).
7. **Tests, evals, security review**: evals through both Open WebUI modes
   (23/23), a sandbox-escape review with 6 issues fixed
   ([SECURITY-REVIEW.md](SECURITY-REVIEW.md)).
8. **Guides**: [python-guide.md](python-guide.md),
   [langgraph-guide.md](langgraph-guide.md), the hands-on checklist
   [TESTING.md](TESTING.md).

## Validation

| Where | Result |
|---|---|
| CI (Linux, macOS, Windows) at `e78bdb1` | Green: tests, evals (LiteLLM mock and the Open WebUI Ollama mode, through `-p` and the VS Code protocol), the sandbox with real bubblewrap (Linux) and `sandbox-exec` (macOS), the extension in a real VS Code, the security job |
| Release build (last run on Phase 4 code, `6427233`) | Green on Windows x64, Linux x64 and macOS arm64, with the standalone tests and the extension run with the bundled program |
| Real Open WebUI workflow (`bff6c85`) | Green |
| Security review ([SECURITY-REVIEW.md](SECURITY-REVIEW.md)) | Bandit, pip-audit, npm audit, and a manual sandbox-escape review: 6 issues fixed (2 high: local services' sockets, `.git`), each with a test that fails on the earlier code |
| Your Windows machine (TESTING.md) | Parts A–D and the optional parts passed on 4 October (install, LiteLLM, Open WebUI, the standalone build). Part E (the sandbox under WSL2) was waiting on WSL on your machine; you marked the phase complete on 5 October |

## Changed from the plan

- **Dropped at the start** (3 October): SSO login, and adapters for the
  OpenAI Responses and Anthropic APIs (both gateways speak chat
  completions).
- **Decided during the phase**: sandboxed commands are auto-allowed; the
  sandbox's network is blocked except an allowlist; all of `.git` is
  read-only in the sandbox (after the security review); the classic UI stays
  the default.
- **Added**: branding (item 6, 4 October).
- **Built differently**: telemetry writes OTLP JSON itself instead of using
  the OpenTelemetry SDK (about ten packages for a handful of counters); the
  standalone build is a folder, not one file (starts faster, and antivirus
  software dislikes programs unpacked to a temp folder on every run).

## Carried forward

- **Code signing** of the standalone program and the extension needs a
  company certificate; until then Windows SmartScreen or macOS Gatekeeper may
  warn on first start.
- **Native Windows has no sandbox** (by design, as in Claude Code): run
  cmcoder in WSL2 for it; permission prompts remain the protection otherwise.
- **The sandbox on your machine under WSL2** (TESTING.md part E), when WSL
  works there: the same Linux code CI tests with real bubblewrap.

## Guides

- [python-guide.md](python-guide.md): the Phase 4 code for a Python developer.
- [langgraph-guide.md](langgraph-guide.md): the Phase 4 features mapped to LangChain/LangGraph.
- [openwebui.md](openwebui.md): setting up Open WebUI as the gateway.
- [branding.md](branding.md): putting the company's name and icon on it.
