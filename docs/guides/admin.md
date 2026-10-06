# For administrators: building and handing out cmcoder

Everything developers install is built by GitHub Actions in this repository,
tested in the real IDEs, and collected into one **release bundle**. You hand
out that bundle (or the files in it); developers need no Python.

## 1. Before the first release: your company's details

| What | Where | Notes |
|---|---|---|
| Name, icon, publisher | `branding/` (`brand.json`, `icon.png`, `icon-mono.svg`) | [../phase4/branding.md](../phase4/branding.md). Every file in the bundle carries them. |
| Gateway, models | a company settings file, or each developer's [setup.md](setup.md) | developers can also be given `settings.json` with the `baseUrl` |
| Rules developers can't loosen | `managed-settings.json` on each PC | see below |
| Code signing | not yet: needs a company certificate | until then, Windows SmartScreen, antivirus and macOS Gatekeeper may warn; tell developers (the guides say so) |

## 2. Build a release

1. Set the version in `pyproject.toml` (`version = "0.6.0"`) and in the
   plugin manifests. `tests/test_versions.py` lists them and fails until all
   agree.
2. Commit, then tag and push: `git tag v0.6.0 && git push origin v0.6.0`.
   (Or run **Actions → Release build → Run workflow** without a tag.)
3. The workflow runs on Windows, macOS and Linux. For each platform it:
   - builds the standalone cmcoder and tests it with no Python;
   - builds each IDE's file around it;
   - runs each IDE's **gate** in the real IDE (VS Code, Visual Studio,
     Eclipse, NetBeans), with the scripted model server and no Python on the
     PC's path.
4. Only if **every** job passed, the **Release bundle** job assembles the
   bundle. With a tag it also makes a GitHub release with the files.

## 3. Read the gate report

`GATE-REPORT.md` (in the bundle, in the release notes, and on the run's
summary page) has one row per check and one column per platform:
- ✅ means passed;
- — means not run there (for example Visual Studio exists only on Windows).

A failed check means no bundle at all. **Not covered by automated tests**
lists what a person should know (signing, a real gateway, older IDE versions
on some platforms).

## 4. Hand it out

The bundle:

```
README-FIRST.txt       which file for which IDE, the two-line install
GATE-REPORT.md         what was tested
SHA256SUMS.txt         checksums of every file
terminal/              cmcoder-terminal-<platform>.zip
vscode/                cmcoder-<platform>.vsix
visualstudio/          cmcoder-visualstudio-win32-x64.vsix
eclipse/               cmcoder-eclipse.zip (all platforms)
netbeans/              cmcoder-netbeans-<platform>.nbm
```

Put it on a share or the intranet with the guides
([README.md](README.md) and the pages it links). Developers install the file
for their IDE; updates are a new file over the old one. A problem report
should include the output of **Copy Diagnostics**, which never contains keys.

## 5. Managed settings (rules developers can't change)

An admin-only file on each PC:

| OS | File |
|---|---|
| Windows | `C:\Program Files\cmcoder\managed-settings.json` |
| macOS | `/Library/Application Support/cmcoder/managed-settings.json` |
| Linux | `/etc/cmcoder/managed-settings.json` |

It applies to the terminal and every IDE alike. It can:
- lock cmcoder to the company gateway (`lockProviders`);
- turn off `bypassPermissions`;
- deny commands, or allow only its own rules;
- allow or deny MCP servers;
- turn off code search, or require the company's Chroma server.

Start from [../managed-settings.example.json](../managed-settings.example.json).
`cmcoder doctor` shows what's enforced on a PC. A broken file stops cmcoder
from starting, so test it on one PC first.

## 6. Code search with a shared Chroma server (optional)

See [../phase5/code-search.md](../phase5/code-search.md), "A shared Chroma
server for a team". Read the security notes there first. Developers can
also run Chroma on their own PC by its URL ([code-search.md](code-search.md)).
