# MCP servers: more tools for the agent

[MCP](https://modelcontextprotocol.io) servers give cmcoder new tools: your
issue tracker, wiki, databases, internal APIs. A server's tools appear to the
model as `mcp__<server>__<tool>`, and its prompts as slash commands.

## Add a server for yourself

```bash
# a remote server (Streamable HTTP; --transport sse for an SSE one)
cmcoder mcp add jira --url https://mcp.example.com/jira -H "Authorization: Bearer ${JIRA_TOKEN}"

# a local server, started by cmcoder (everything after -- is its command)
cmcoder mcp add files -- npx -y @modelcontextprotocol/server-filesystem C:\docs

cmcoder mcp list --check      # start them and list their tools
cmcoder mcp remove files
```

They are saved under `mcpServers` in `~/.cmcoder/settings.json`. `${VAR}`
(or `${VAR:-default}`) is read from the environment when the server starts,
so **tokens stay out of files**. `-e NAME=value` sets an environment variable
for a local server.

For one run only: `cmcoder --mcp-config servers.json`.

## Share servers with the team

Put them in `.mcp.json` at the repository root (Claude Code's format) and
commit it:

```json
{
  "mcpServers": {
    "jira": {
      "url": "https://mcp.example.com/jira",
      "headers": { "Authorization": "Bearer ${JIRA_TOKEN}" }
    },
    "db": {
      "command": "npx",
      "args": ["-y", "@example/mcp-postgres"],
      "env": { "DATABASE_URL": "${DATABASE_URL}" }
    }
  }
}
```

Each developer keeps their own token in their environment (`JIRA_TOKEN`).

A repository's servers are used only in a **trusted** project, and each one
asks once before it first starts (`cmcoder mcp approve NAME` approves it
ahead of time); a changed command asks again
([permissions.md](permissions.md#trusting-a-project)).

| Server setting | Meaning |
|---|---|
| `command`, `args`, `env`, `cwd` | a local server (stdio) |
| `url`, `headers`, `type` | a remote server: `"http"` (the default for a URL) or `"sse"` |
| `caCertPath` | the server's certificate authority (the OS store is trusted already) |
| `startupTimeout`, `toolTimeout` | seconds to start and list tools (30), per tool call (120) |
| `disabled` | keep it in the file, but don't start it |

## Use it

- The model calls the tools when they help ("create a ticket for this bug").
- They **ask before running**, unless an allow rule says otherwise:
  `"allow": ["mcp__jira"]` for all of a server's tools,
  `"mcp__jira__create_issue"` for one ([permissions.md](permissions.md)).
- A server's prompts are commands: `/mcp__<server>__<prompt> args`.
- `/mcp` in the chat shows each server's state. A server's own output goes to
  `~/.cmcoder/logs/mcp-<name>.log`.

## With the other features

- **Subagents:** give a role a server's tools with `tools: ..., mcp__jira`
  ([agents.md](agents.md)); a PM role can then read and write tickets
  ([team.md](team.md)).
- **Hooks:** `PreToolUse` with `"matcher": "mcp__jira__.*"` can check or block
  a server's calls ([hooks.md](hooks.md)).
- **Administrators** can allow or deny servers for everyone with
  `allowedMcpServers` / `deniedMcpServers` in the managed settings
  ([../admin/README.md](../admin/README.md#5-managed-settings-rules-developers-cant-change)).

## If it doesn't work

| Symptom | Fix |
|---|---|
| The server isn't listed | in a repository's `.mcp.json`: trust the project (`cmcoder trust`), then approve the server |
| "failed to start" | run its command yourself in a terminal; read `~/.cmcoder/logs/mcp-<name>.log` |
| 401/403 from a remote server | the `${VAR}` in its headers isn't set in the environment cmcoder started from |
| A tool always asks | add an allow rule for it, or answer **Always allow** once |
