# The VS Code extension: build, release, install

**What you make:** `cmcoder-<platform>.vsix`, the extension with the
standalone `cmcoder` inside. Developers install this one file into VS Code.
They need no Python, Node.js or administrator rights: VS Code installs
extensions for the user.

| Platform | File |
|---|---|
| Windows x64 | `cmcoder-win32-x64.vsix` |
| macOS, Apple silicon | `cmcoder-darwin-arm64.vsix` |
| Linux x64 | `cmcoder-linux-x64.vsix` |

Build each platform's file on that platform. Hand out only these files,
**never `cmcoder.vsix`**: that one (from `npm run package`, or CI's
`cmcoder-vsix` artifact) is a test build without cmcoder inside.

## 1. The build machine

| Needs | Why |
|---|---|
| Windows 10/11 x64, macOS on Apple silicon, or Linux x64 | the platform you build for |
| **Git** (on Windows: Git for Windows) | the build is named after its commit |
| **uv** ([docs.astral.sh/uv](https://docs.astral.sh/uv/)) | builds cmcoder; it downloads its own Python 3.12 |
| **Node.js 22 LTS** (with npm) | builds the chat page and packs the extension |
| Internet access to PyPI and the npm registry | the build's packages |

Install uv as in [cli.md](cli.md#1-the-build-machine), and Node.js 22 from
your company's software portal or [nodejs.org](https://nodejs.org/).

## 2. Get the source

```
git clone <your repository URL> cmcoderagent
cd cmcoderagent
git checkout <the branch or tag to build>
```

Use a git clone, not a copied folder (the build is named after its commit).
Put your company's details in `branding/` first
([README.md](README.md#before-the-first-build-your-companys-details)).

## 3. Build and pack (one command)

```
# Windows (PowerShell)
powershell -ExecutionPolicy Bypass -File packaging\package-vscode.ps1

# macOS, Linux
bash packaging/package-vscode.sh
```

The script does what the Release build does for this platform:

1. checks the tools and that git can name the build;
2. builds the standalone cmcoder with PyInstaller;
3. runs it with no Python in reach (`tests/test_standalone.py`);
4. builds the shared chat page;
5. packs the extension with cmcoder and your branding;
6. checks `cmcoder` is inside the file, and prints the file's path and
   SHA256.

The output is `vscode/cmcoder-<platform>.vsix`.

`-SkipTests` (Windows) or `--skip-tests` leaves out step 3. Use it only
while trying things: a release should always pass it.

### The same steps by hand

If you can't run the script, these are its steps:

```
uv sync
uv run --with pyinstaller python packaging/build.py
# check it without Python: see cli.md, section 4
cd clients/web-panel && npm ci && npm run build && cd ../..
cd vscode && npm ci && cd ..
uv run python packaging/vsix.py
```

## 4. Check it before handing it out

1. The script's last lines show the path and SHA256, and say cmcoder is inside.
2. On a test PC without Python (a clean VM is best), install the file
   (section 6), open a folder, open the chat, and ask something: it answers,
   and the top of the chat shows the model. **cmcoder: Show Log** (Command
   Palette) shows which cmcoder started: the one in the extension or its
   shared copy in `.cmcoder/bin`.

## 5. Release it

- **With the other packages:** the Release build makes all three platforms'
  files and runs each in a real VS Code. They go in the bundle's `vscode/`
  folder ([release.md](release.md)).
- **On its own:** hand out `vscode/cmcoder-<platform>.vsix` and its SHA256
  (printed by the script). The file isn't code-signed: VS Code installs it
  anyway, but antivirus may look at `cmcoder.exe` inside it.

Version: the extension's version is `vscode/package.json`'s, which must match
`pyproject.toml` (`tests/test_versions.py` checks it). Raise both for a new
release.

## 6. On developers' PCs: install

**They need:**
- **VS Code 1.90 or newer**;
- on Windows, **Git for Windows** (shell commands run in Git Bash);
- network access to the AI gateway.

**Install:**
1. In VS Code: **Extensions** (Ctrl+Shift+X) → **⋯** (top right of the
   panel) → **Install from VSIX…** → choose `cmcoder-<platform>.vsix`.
   Or, in a terminal: `code --install-extension cmcoder-win32-x64.vsix`.
2. Reload when asked. The cmcoder icon appears in the Activity Bar.
3. Open a folder and open the chat. The first time, the extension copies its
   cmcoder to `%USERPROFILE%\.cmcoder\bin` (`~/.cmcoder/bin`): the one copy
   the terminal and every IDE use.
4. First time only, unless your company's settings came with the build:
   `cmcoder setup` in a terminal (the program's full path if the terminal
   package isn't installed; [../guides/setup.md](../guides/setup.md)).

**Update:** install the new `.vsix` the same way; VS Code replaces the old
version.

**Remove:** **Extensions** → cmcoder → **Uninstall**. Settings, keys and
conversations stay in `~/.cmcoder`.

**For IT (scripted installs):**
`code --install-extension \\share\cmcoder\cmcoder-win32-x64.vsix --force`
installs for the user who runs it.

Leave the `cmcoder.executable` setting empty: the extension then uses its own
copy.

User guide: [../guides/vscode.md](../guides/vscode.md).

## If the build fails

| Problem | Fix |
|---|---|
| "git can't read this folder's commit" | "dubious ownership": run the `git config --global --add safe.directory …` line it prints. "Not a git repository": use a git clone |
| "Node.js 22 or newer is needed" | install Node.js 22 LTS and open a new terminal |
| `test_standalone.py` fails | don't hand the file out; send the test output to the maintainers |
| "cmcoder is missing from the .vsix" | the program build failed earlier in the output: fix that first |
| npm errors behind a proxy | set `HTTPS_PROXY`, or `npm config set proxy …` / `https-proxy …` |
