# Phase 3 — Extensibility: plan

**Goal** ([DESIGN.md §16](../DESIGN.md#16-roadmap)): teams can customise
cmcoder without forking it. **Done when:** a team can add tools (MCP),
automatic checks (hooks), shortcuts (slash commands), helpers (subagents)
and know-how (skills) through files, and they work the same in the CLI and in
VS Code.

## Decisions (3 October 2026)

| Question | Decision |
|---|---|
| Bash sandbox | **Moved to Phase 4**, built once for Linux, macOS and Windows together. Phase 3 is everything that works on Windows today. |
| MCP servers, hooks and agents from a repository | Used only in a **trusted** project (`cmcoder trust`, or a workspace VS Code trusts), and **each project MCP server and hook is approved once** before it first runs, like Claude Code. Your own (user-level) ones need nothing. |
| Kinds of MCP servers | **Local (stdio) and remote (Streamable HTTP, plus legacy SSE)**. Administrators can allow or deny servers in managed settings. |
| MCP library | The **official `mcp` Python SDK** (2.x): follows protocol changes, transports and remote-server auth for us. It adds about 28 packages, all covered by the CI `security` job. |

## Items, in order

### 1. MCP client

- Servers from `mcpServers` in your user settings and from a project's
  `.mcp.json` (Claude Code's format: `command`/`args`/`env` or `url`/`headers`;
  `${VAR}` expanded from the environment, so secrets stay out of files).
- Tools appear to the model as `mcp__<server>__<tool>` and go through the
  permission engine: they ask by default; allow rules like
  `mcp__github` (whole server) or `mcp__github__create_issue`.
- Resources (read with a tool) and prompts (as slash commands).
- `/mcp` and `cmcoder mcp list|add|remove`; servers start lazily, with
  timeouts and output limits; a broken server doesn't stop cmcoder.
- Project servers: trusted project + one-time approval each.
  Managed settings: `allowedMcpServers` / `deniedMcpServers`.

**Status: done.** `src/cmcoder/mcp_client.py`, `cli/mcp_cmd.py`; tests in
`tests/test_mcp.py` against a real MCP server (`tests/mcp_servers/demo_server.py`)
over stdio and HTTP.
- Each server runs in its own task for its whole life (the SDK's connections
  must be opened and closed in one task); servers start at the beginning of
  the first turn; one that fails is reported and skipped.
- Server programs are found on PATH with `find_program` (the SDK's own lookup
  uses `shutil.which`, which on Windows looks in the project folder first).
- A server's stderr goes to `~/.cmcoder/logs/mcp-<name>.log`, so it never
  mixes with the terminal or the VS Code protocol.
- Remote servers use cmcoder's TLS setup (OS trust store, `caCertPath`) and
  proxy settings.
- Project servers: approval is tied to the server's exact configuration and
  asked through the normal permission prompt (CLI, TUI, VS Code); without
  anyone to ask (`-p`) they stay off.
- `cmcoder doctor` has an "MCP servers" section; `cmcoder trust` lists a
  project's servers.
- Prompts as slash commands come with item 3.

### 2. Hooks

- Shell commands on `PreToolUse`, `PostToolUse`, `UserPromptSubmit`,
  `SessionStart`, `Stop`, `SubagentStop`, `PreCompact`, `Notification`, with
  tool-name matchers, configured under `hooks` in settings.
- They get the event as JSON on stdin; exit code 2 (or JSON output) blocks the
  action and tells the model why; other output can add context.
- Run with the same shell as the Bash tool (Git Bash on Windows), with a
  timeout. Project hooks: trusted project + one-time approval each. Managed
  setting `allowManagedHooksOnly`.

**Status: done.** `src/cmcoder/core/hooks.py`, hook points in `core/agent.py`;
`tests/test_hooks.py` (16 tests with real bash hooks).
- PreToolUse runs after the permission check's deny rules: a hook can block
  anything, and its "allow" only skips a normal prompt (never a deny rule or a
  high-risk command); "ask" makes a normally allowed call ask.
- Stop: a blocking hook sends the model back to work at most 3 times a turn.
- UserPromptSubmit: a blocked prompt never reaches the model.
- Notification runs in the background, so it can't delay a permission prompt.
- Managed hooks are kept apart from yours (never merged away); project hooks
  are approved per command, like MCP servers. `cmcoder doctor` lists hooks.
- `SubagentStop` comes with item 4.

### 3. Custom slash commands

- Markdown files in `.cmcoder/commands/` and `~/.cmcoder/commands/`
  (subfolders become namespaces), with `$ARGUMENTS` / `$1`, and frontmatter
  (`description`, `argument-hint`, `model`, `allowed-tools`).
- They expand to a prompt; no command runs shell code on its own.
- Listed in `/help`, completed in the REPL, the TUI and the VS Code panel.

### 4. Subagents (`Task` tool)

- The model can hand a self-contained job to a subagent with its own
  conversation, tools and model, and gets its final report back.
- Built in: `general-purpose` (main model) and `explore` (read-only tools,
  `smallFastModel` or `subagentModel`). Custom ones in `.cmcoder/agents/*.md`
  and `~/.cmcoder/agents/` (name, description, tools, model, prompt).
- Same permissions as the main agent; no nested subagents; `SubagentStop`
  hook; shown as a collapsible card in every front end.

### 5. Skills

- Folders with a `SKILL.md` (name, description in frontmatter) in
  `.cmcoder/skills/` and `~/.cmcoder/skills/`. Only the descriptions are in
  the prompt; a `Skill` tool loads the rest when the model decides it's
  relevant, and the skill's other files can be read as usual.

### 6. Front ends

- VS Code: slash-command completion, subagent cards, MCP and hook approval
  prompts, `/mcp` status. The protocol gets the events these need; the
  generated TypeScript types follow.
- `cmcoder doctor` and `cmcoder trust` list MCP servers, hooks, commands,
  agents and skills, and what trust would enable.

### 7. Tests, evals and security review

- Real MCP servers in the tests (stdio and HTTP, written with the same SDK),
  hooks on Windows (Git Bash) and POSIX, subagents and skills with the mock
  model; new eval tasks that need a custom command, a subagent or an MCP tool,
  run through both `-p` and the VS Code protocol.
- A SAST/SCA review of the new code and packages, as in Phase 2.

### 8. Guides and docs

- `python-guide.md` and `langgraph-guide.md` sections per item (LangChain's
  MCP adapters, LangGraph subgraphs, middleware/hooks); a "customising
  cmcoder" page with examples; `STATUS.md` at the end.

## Not in Phase 3

Bash and Windows sandboxing (Phase 4, with SSO, OpenTelemetry, a standalone
binary). Output styles and the status line (later). Plugins and marketplaces.

## Checklist

- [x] 1. MCP client
- [x] 2. Hooks
- [ ] 3. Custom slash commands
- [ ] 4. Subagents (`Task`)
- [ ] 5. Skills
- [ ] 6. Front ends (VS Code, doctor, trust)
- [ ] 7. Tests, evals and security review
- [ ] 8. Guides and docs
- [ ] Hands-on use on Windows (CLI and VS Code), and `STATUS.md`
