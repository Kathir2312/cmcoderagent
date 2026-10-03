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

### 2. Carry-overs

- VS Code panel: `/rewind`, `/cost`, `/model`, `/todos`.
- Decide whether the full-screen TUI becomes the default.
- Node.js 22 for building the extension.

### 3. Bash sandbox

- Linux: bubblewrap; macOS: `sandbox-exec`; Windows: inside WSL2 (the Linux
  path). Writes only in the project and temp folders; network only to allowed
  hosts.
- On where available; `doctor` and the permission prompt say whether a
  command runs sandboxed. Native Windows unchanged.
- Decided when the item starts: may sandboxed commands skip the permission
  prompt (Claude Code's auto-allow)?

### 4. OpenTelemetry (off by default)

- OTLP export configured in settings or managed settings (standard `OTEL_*`
  variables too): sessions, tokens and cost per model, tool calls by name and
  permission decision, errors, latency.

### 5. Standalone binary and per-platform VS Code extension

- PyInstaller builds for Windows x64, Linux x64, macOS arm64; each `.vsix`
  carries its `cmcoder` (PATH as fallback), so users need no Python or uv.
- Built in CI as downloads; code signing only with a company certificate.

### 6. Tests, evals and security review

- Evals through the Open WebUI mock; sandbox tests that try to escape.
- SAST/SCA as in Phases 2–3, plus a sandbox escape review.

### 7. Guides and docs

- Guide sections per item, the Open WebUI setup page; hands-on checklist on
  Windows with LiteLLM and Open WebUI; `STATUS.md`.

## Not in Phase 4

SSO, other API formats (decided above). Other IDEs, output styles and a
status line, plugins and marketplaces, image/PDF/notebook reading, mTLS
client certificates: later, if needed.

## Checklist

- [ ] 1. Open WebUI gateway
- [ ] 2. Carry-overs (VS Code commands, TUI default, Node 22)
- [ ] 3. Bash sandbox
- [ ] 4. OpenTelemetry
- [ ] 5. Standalone binary and per-platform VSIX
- [ ] 6. Tests, evals and security review
- [ ] 7. Guides and docs
- [ ] Hands-on use on Windows (LiteLLM and Open WebUI), and `STATUS.md`
