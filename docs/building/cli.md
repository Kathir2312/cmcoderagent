# The standalone cmcoder CLI (terminal package): build, release, install

**What you make:** `cmcoder-terminal-<platform>.zip`, holding:
- the standalone `cmcoder` program (it needs no Python);
- install and uninstall scripts for that platform;
- a `README.txt`.

Developers unzip it and run one script. It needs no administrator rights.

| Platform | File |
|---|---|
| Windows x64 | `cmcoder-terminal-win32-x64.zip` |
| macOS, Apple silicon | `cmcoder-terminal-darwin-arm64.zip` |
| Linux x64 | `cmcoder-terminal-linux-x64.zip` |

Build each platform's file on that platform: Windows on Windows, and so on.

## 1. The build machine

| Needs | Why |
|---|---|
| Windows 10/11 x64, macOS on Apple silicon, or Linux x64 | the platform you build for |
| **Git** (on Windows: Git for Windows) | the build is named after its commit (`cmcoder --version` shows `0.1.0 (aa3dc83 2026-10-12)`) |
| **uv** ([docs.astral.sh/uv](https://docs.astral.sh/uv/)) | builds and tests cmcoder; it downloads its own Python 3.12, so Python needn't be installed |
| Internet access to PyPI | uv downloads the Python packages and PyInstaller |
| About 2 GB free disk | the build and its caches |

Install uv:

```
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
# macOS, Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

(Or follow your company's approved way of installing tools.)

## 2. Get the source

```
git clone <your repository URL> cmcoderagent
cd cmcoderagent
git checkout <the branch or tag to build>
```

Build from a **git clone**: a copied folder or a downloaded zip has no
commit, and the build is then named `unknown`. On Windows, if git says
"dubious ownership" (common on a second drive such as `E:`), run the
`git config --global --add safe.directory …` line it prints.

Put your company's details in `branding/` first if you have them
([README.md](README.md#before-the-first-build-your-companys-details)).

## 3. Build cmcoder

```
uv sync
uv run --with pyinstaller python packaging/build.py
```

The output is `dist/cmcoder/`: the `cmcoder` program (`cmcoder.exe` on
Windows) and its `_internal` folder. Keep them together: the program needs the
folder next to it.

## 4. Check it runs without Python

This runs the built program the way developers do: with no Python, pip or
uv reachable on PATH (`CMCODER_TEST_NO_PYTHON=1`).

```
# Windows (PowerShell)
$env:CMCODER_TEST_BINARY = "dist\cmcoder\cmcoder.exe"; $env:CMCODER_TEST_NO_PYTHON = "1"
uv run pytest -q tests/test_standalone.py
Remove-Item Env:CMCODER_TEST_BINARY, Env:CMCODER_TEST_NO_PYTHON

# macOS, Linux
CMCODER_TEST_BINARY=dist/cmcoder/cmcoder CMCODER_TEST_NO_PYTHON=1 uv run pytest -q tests/test_standalone.py
```

Then a quick look by hand:

```
dist/cmcoder/cmcoder --version        # Windows: dist\cmcoder\cmcoder.exe --version
```

It should show the version and your commit, not `unknown`.

## 5. Pack the terminal package

```
uv run python packaging/terminal.py
```

The output is `dist/terminal/cmcoder-<platform>.zip`. Check the install,
update, run and uninstall cycle in a temporary home folder (on Windows the
installer part runs only in CI, because it changes the user's PATH: install it
on a test PC instead, section 7):

```
# Windows (PowerShell)
$env:CMCODER_TEST_BINARY = "dist\cmcoder\cmcoder.exe"
uv run pytest -q tests/test_terminal_package.py
Remove-Item Env:CMCODER_TEST_BINARY

# macOS, Linux
CMCODER_TEST_BINARY=dist/cmcoder/cmcoder uv run pytest -q tests/test_terminal_package.py
```

Give the file its release name:

```
# Windows (PowerShell)
Copy-Item dist\terminal\cmcoder-win32-x64.zip dist\cmcoder-terminal-win32-x64.zip
# macOS (use linux-x64 on Linux)
cp dist/terminal/cmcoder-darwin-arm64.zip dist/cmcoder-terminal-darwin-arm64.zip
```

## 6. Release it

- **With the other packages:** the Release build makes all three platforms'
  terminal packages, tests them, and puts them in the bundle's `terminal/`
  folder ([release.md](release.md)). Use this for real releases.
- **On its own:** hand out `dist/cmcoder-terminal-<platform>.zip` with its
  checksum:

  ```
  Get-FileHash dist\cmcoder-terminal-win32-x64.zip -Algorithm SHA256     # Windows
  shasum -a 256 dist/cmcoder-terminal-*.zip                             # macOS, Linux
  ```

  Before handing it out, install it once on a test PC without Python
  (section 7).

The files aren't code-signed until your company has a certificate, so
Windows SmartScreen, antivirus or macOS Gatekeeper may warn. Tell developers,
or sign `cmcoder.exe` before packing if you have a certificate.

## 7. On developers' PCs: install

**They need:**
- no Python, pip, uv or Node.js;
- on Windows, **Git for Windows** (cmcoder runs shell commands with Git Bash);
- network access to your AI gateway (often the VPN).

**Install:**

1. Unzip `cmcoder-terminal-<platform>.zip` anywhere (for example Downloads).
2. Run the installer inside it:

   | Windows | macOS, Linux |
   |---|---|
   | double-click **`install.cmd`** (or `powershell -ExecutionPolicy Bypass -File install.ps1`) | `sh install.sh` |
   | installs into `%USERPROFILE%\.cmcoder\bin` and adds it to the user's PATH | installs into `~/.cmcoder/bin` and links `~/.local/bin/cmcoder` |

3. Open a **new** terminal and run `cmcoder doctor`.
4. First time only, unless your company's settings came with the package:
   `cmcoder setup` (the gateway, the API key and the models;
   [../guides/setup.md](../guides/setup.md)).

**Update:** run the new version's installer. Close terminals and IDEs that
run cmcoder first: a running program can't be replaced.

**Remove:** `uninstall.cmd` (Windows) or `sh uninstall.sh`. Settings,
conversations and keys stay in `~/.cmcoder`.

**For IT (scripted installs):**
- Windows: `powershell -ExecutionPolicy Bypass -File install.ps1 -Destination D:\Tools\cmcoder`
- macOS, Linux: `CMCODER_INSTALL_DIR=/opt/tools/cmcoder sh install.sh`
- If scripts are blocked, copy the zip's `cmcoder` folder anywhere and add it
  to PATH.

The installer runs for the current user. Don't run it "as administrator" for
someone else: the files would land in the administrator's profile.

The IDE packages carry their own cmcoder and put it in the same
`.cmcoder/bin` folder, so developers who only use an IDE don't need this
package.

User guide: [../guides/terminal.md](../guides/terminal.md).

## If the build fails

| Problem | Fix |
|---|---|
| `--version` shows `unknown` | build from a git clone; on Windows, the `safe.directory` line git prints |
| "PyInstaller is needed" | run the build through `uv run --with pyinstaller …` as above |
| A branding error (template, managed settings) | fix the named file in `branding/`; the message says what's wrong |
| `test_standalone.py` fails with "the standalone program started Python" | something in the build needs Python at run time: don't hand the file out; report it with the test's output |
| Antivirus quarantines `dist/cmcoder` | exclude the build folder on the build machine; on developers' PCs, ask IT to allow `cmcoder.exe` |
