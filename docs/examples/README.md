# Examples to start from

| File | What | Used by |
|---|---|---|
| [settings.example.json](settings.example.json) | the common settings, with examples | developers (a copy lands in `~/.cmcoder/` on first start); the standalone build packs it ([../features/settings.md](../features/settings.md)) |
| [managed-settings.example.json](managed-settings.example.json) | a starting point for rules developers can't change | IT; the release bundle's `admin/` folder carries it when `branding/managed-settings.json` doesn't exist ([../admin/README.md](../admin/README.md#5-managed-settings-rules-developers-cant-change)) |
| [team/](team/README.md) | a complete team: roles, skills, the `/feature` command, hooks, MCP | team leads ([../features/team.md](../features/team.md)) |

The two JSON files are read by the build (`packaging/build.py`,
`packaging/bundle.py`) and by cmcoder itself (`src/cmcoder/user_folder.py`):
keep them valid JSON, with example addresses only.
