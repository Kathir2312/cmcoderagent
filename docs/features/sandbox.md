# The Bash sandbox

On Linux, WSL2 and macOS the agent's Bash commands run in a **sandbox**
(bubblewrap on Linux and WSL2, `sandbox-exec` on macOS). A sandboxed command
can't reach what it shouldn't, so it runs **without asking**:

- it can write only inside the project and a temp folder;
- `.git` (all of it), `.cmcoder`, `.vscode`, `.idea` and `.mcp.json` stay
  read-only;
- credential folders (`~/.ssh`, `~/.aws`, ...) and local services (Docker,
  the desktop session) are out of reach;
- the network is reachable only through cmcoder's proxy, which allows the
  hosts you list.

Git commands that read (`status`, `diff`, `log`) work in the sandbox; ones
that write (`commit`, `checkout`, `stash`) run outside it, with your approval.
Deny rules, high-risk commands and plan mode work as before
([permissions.md](permissions.md)). If a command really needs more, the model
can ask to run it outside the sandbox: that always asks you.

**Native Windows has no sandbox:** commands ask as usual. Run cmcoder inside
WSL2 to get one there.

## Settings

```json
"sandbox": {
  "network": { "allowedHosts": ["pypi.org", "*.pythonhosted.org", "registry.npmjs.org", "github.com"] },
  "writablePaths": ["~/.m2"],
  "autoAllow": true,
  "allowUnsandboxedCommands": true
}
```

| Setting | Meaning |
|---|---|
| `network.allowedHosts` | the hosts commands may reach (wildcards allowed), e.g. your package registries |
| `network.allowLocalhost` | macOS: let commands reach any local port (a test server they start), including services outside the sandbox |
| `writablePaths` | more folders commands may write (build caches such as `~/.m2`, `~/.gradle`) |
| `denyReadPaths` | more folders commands may not read |
| `autoAllow` | sandboxed commands run without asking (default) |
| `allowUnsandboxedCommands` | the model may ask to run a command outside the sandbox (always asks you) |
| `enabled` | `false` turns it off (or `CMCODER_SANDBOX=off`) |

A repository's settings can't change any of this unless the project is
trusted; managed settings can force it on for everyone
([../admin/README.md](../admin/README.md#5-managed-settings-rules-developers-cant-change)).

## Requirements

| OS | Needs |
|---|---|
| Linux, WSL2 | `bubblewrap` (`sudo apt install bubblewrap`). Ubuntu 24.04 also needs unprivileged user namespaces allowed for it |
| macOS | nothing (`sandbox-exec` is built in) |
| Windows | no sandbox; use WSL2 for one |

`cmcoder doctor` shows whether it is active, and why not.

## For the team

Put the hosts and folders your builds need in the project's
`.cmcoder/settings.json` (registries, build caches), so everyone's builds work
in the sandbox from the first run.
