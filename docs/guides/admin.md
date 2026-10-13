# For administrators: building and handing out cmcoder

Everything developers install is built by GitHub Actions in this repository,
tested in the real IDEs, and collected into one **release bundle**. You hand
out that bundle (or the files in it); developers need no Python.

## 1. Before the first release: your company's details

| What | Where | Notes |
|---|---|---|
| Name, icon, publisher | `branding/` (`brand.json`, `icon.png`, `icon-mono.svg`) | [../phase4/branding.md](../phase4/branding.md). Every file in the bundle carries them. |
| Gateway, models | `branding/settings.template.json` (optional) | every developer's `settings.json` starts as a copy of it, so they only add their API key; without it, each runs `cmcoder setup` ([setup.md](setup.md)) |
| Rules developers can't loosen | `branding/managed-settings.json` (optional), installed on each PC | see section 5 |
| Code signing | not yet: needs a company certificate | until then, Windows SmartScreen, antivirus and macOS Gatekeeper may warn; tell developers (the guides say so) |

### The settings template

`branding/settings.template.json` is a normal `settings.json` (start from
[../settings.example.json](../settings.example.json)): your gateway, its
certificate if needed, the models. It goes into the program, and the first
time cmcoder starts on a PC (terminal or IDE) it becomes that developer's
`%USERPROFILE%\.cmcoder\settings.json`. From then on the file is the
developer's: a later build never overwrites it (delete it to get the
template again). No API key goes in it: keys go to each developer's keychain.

The build refuses a template (or a managed file) that isn't valid JSON, isn't
valid settings, or still has an example address.

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
jetbrains/             cmcoder-jetbrains-<platform>.zip (IntelliJ IDEA, PyCharm, Rider, …)
admin/                 managed settings and the scripts that install them
```

Put it on a share or the intranet with the guides
([README.md](README.md) and the pages it links). Developers install the file
for their IDE; updates are a new file over the old one. A problem report
should include the output of **Copy Diagnostics**, which never contains keys.

### Where cmcoder runs from

Every developer has one cmcoder, in `%USERPROFILE%\.cmcoder\bin`
(`~/.cmcoder/bin` on macOS and Linux), whichever way they installed it:

- the terminal package installs it there (no administrator rights; an
  older install in `%LOCALAPPDATA%\Programs\cmcoder` is moved);
- each IDE plugin carries its own copy, and the first time it starts it
  copies that one there, if it's newer than what's there; the IDEs then run
  it from there.

For antivirus or AppLocker rules: day to day, what runs is
`%USERPROFILE%\.cmcoder\bin\cmcoder.exe` (unsigned until the company has a
certificate). An IDE runs the copy inside its plugin folder only until that
one has been copied over. If programs can't run from plugin folders, install
the terminal package first (same version or newer): the IDEs then run the
shared copy from the start. A running cmcoder is never replaced: the update
waits until it has closed.

**Roaming profiles:** the program is large (it carries everything it
needs). If `%USERPROFILE%` roams, leave `.cmcoder\bin` out of the roaming
profile (Group Policy: *Exclude directories in roaming profile*); the IDEs
put it back on each PC. The saved conversations in `.cmcoder\projects` can
roam or not, as you prefer.

## 5. Managed settings (rules developers can't change)

An admin-only file on each PC:

| OS | File |
|---|---|
| Windows | `C:\Program Files\cmcoder\managed-settings.json` |
| macOS | `/Library/Application Support/cmcoder/managed-settings.json` |
| Linux | `/etc/cmcoder/managed-settings.json` |

It applies to the terminal and every IDE alike. It can:
- lock cmcoder to the company gateway (`lockProviders`);
- turn off `bypassPermissions` or the `auto` mode (`disableBypassPermissionsMode`,
  `disableAutoMode`: `"disable"`);
- deny commands, or allow only its own rules;
- allow or deny MCP servers;
- turn off code search, or require the company's Chroma server.

Start from [../managed-settings.example.json](../managed-settings.example.json).
`cmcoder doctor` shows what's enforced on a PC. A broken file stops cmcoder
from starting, so test it on one PC first.

To roll it out, put your file in `branding/managed-settings.json` before the
build (it is checked like the template, and it lands in the bundle's
`admin/` folder), or next to the scripts in `admin/` afterwards:

- **One PC:** right-click `admin\install-managed.cmd`, **Run as
  administrator** (macOS, Linux: `sudo sh admin/install-managed.sh`). It
  checks the file and copies it to the place above.
- **Many PCs:** deploy the same file to the same place with Intune, Group
  Policy, Configuration Manager or Jamf.

Developers' installers never install it: they run without administrator
rights, and an installer started "as administrator" with another account
would put that account's files in the wrong profile.

## 6. Code search with a shared Chroma server (optional)

See [../phase5/code-search.md](../phase5/code-search.md), "A shared Chroma
server for a team". Read the security notes there first. Developers can
also run Chroma on their own PC by its URL ([code-search.md](code-search.md)).

## 7. Build the VS Code file yourself (without the Release workflow)

When you need just the VS Code extension, for example to try a branding
change, build it on a build PC with one command. Developers' PCs need nothing
but VS Code: cmcoder is inside the file. The build PC needs **uv**
(<https://docs.astral.sh/uv/>, one program; it downloads its own Python, so
Python needn't be installed), **Node.js 22** and **Git**, with the repository
as a **git clone** (`git clone`, then check out the branch): the build is named
after its commit, which a copied folder or a downloaded zip doesn't have. Build
on the platform you build for: Windows for `cmcoder-win32-x64.vsix`.

```
powershell -ExecutionPolicy Bypass -File packaging\package-vscode.ps1     # Windows
bash packaging/package-vscode.sh                                          # macOS, Linux
```

It builds cmcoder (PyInstaller), runs it with no Python in reach, builds the
chat panel, packages the extension with cmcoder and your `branding/`, checks
the program is in the file, and prints the file's path and SHA256.
`-SkipTests` (`--skip-tests`) leaves out the test run.

If it stops with "git can't read this folder's commit": for "dubious
ownership" (common on a second drive such as `E:`), run the
`git config --global --add safe.directory …` line it prints; for "not a git
repository", use a git clone instead of a copied folder. (With `-SkipTests` it
goes on, and the build says `unknown` instead of its commit.)

Hand out `vscode\cmcoder-win32-x64.vsix`, never `cmcoder.vsix`: that one (from
`npm run package`, or the CI workflow's `cmcoder-vsix` artifact) is a test
build without cmcoder inside. The Release workflow (section 2) remains the
way to build everything, tested in the real IDEs.
