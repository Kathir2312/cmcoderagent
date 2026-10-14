# The Visual Studio extension: build, release, install

**What you make:** `cmcoder-visualstudio-win32-x64.vsix`, the Visual Studio
2022 extension with the standalone `cmcoder.exe` inside. Developers
double-click it. They need no Python and no Node.js.

Visual Studio exists only on Windows, so there is one file, built on Windows.

## 1. The build machine

| Needs | Why |
|---|---|
| **Windows 10/11 x64** | Visual Studio and the Windows cmcoder |
| **Visual Studio 2022 or newer** (any edition) with the **"Visual Studio extension development"** workload; or the **Build Tools for Visual Studio** with "MSBuild" and the ".NET Framework 4.8 targeting pack" | builds the extension (MSBuild; the VS SDK comes from NuGet) |
| **Git for Windows** | the build is named after its commit |
| **uv** ([docs.astral.sh/uv](https://docs.astral.sh/uv/)) | builds cmcoder; it downloads its own Python 3.12 |
| **Node.js 22 LTS** (with npm) | builds the shared chat page |
| Internet access to PyPI, the npm registry and nuget.org | the build's packages |
| (optional) **.NET 8 SDK** | only to run the extension core's tests |

To add the workload to an installed Visual Studio: **Visual Studio
Installer → Modify → Workloads → Visual Studio extension development**.

Run the commands below in **PowerShell**, in the repository folder.

## 2. Get the source

```
git clone <your repository URL> cmcoderagent
cd cmcoderagent
git checkout <the branch or tag to build>
```

Use a git clone (the build is named after its commit). Put your company's
details in `branding/` first
([README.md](README.md#before-the-first-build-your-companys-details)).

## The quick way: one command

```
powershell -ExecutionPolicy Bypass -File packaging\package-visualstudio.ps1
```

The script does sections 3 to 5 in one go, as the Release build does:

1. checks the tools first (Visual Studio's MSBuild and the .NET Framework
   4.8 targeting pack, Node.js 22) and that git can name the build, so a
   missing one stops it before the long steps;
2. builds the standalone cmcoder and runs it with no Python in reach;
3. builds the shared chat page;
4. puts a fresh copy of cmcoder into the extension and builds it with
   `visualstudio\build-vsix.ps1`;
5. checks `cmcoder.exe` and the chat page are inside the file, and prints
   the file's path and SHA256.

The output is `dist\cmcoder-visualstudio-win32-x64.vsix`. Then go on with
section 6.

| Option | What it does |
|---|---|
| `-SkipTests` | leaves out the test run. Only for try-outs: a release always passes it |
| `-UseBuilt` | uses `dist\cmcoder` from an earlier run (built and checked then) instead of building it again |

`packaging\package-all.ps1` builds this and the other Windows packages in
one go ([README.md](README.md#one-command-per-package)).

Sections 3 to 5 are the same steps by hand, for when you can't run the
script.

## 3. Build cmcoder and check it without Python

```
uv sync
uv run --with pyinstaller python packaging/build.py

$env:CMCODER_TEST_BINARY = "dist\cmcoder\cmcoder.exe"; $env:CMCODER_TEST_NO_PYTHON = "1"
uv run pytest -q tests/test_standalone.py
Remove-Item Env:CMCODER_TEST_BINARY, Env:CMCODER_TEST_NO_PYTHON
```

The output is `dist\cmcoder\` (`cmcoder.exe` and its `_internal` folder).

## 4. Build the chat page

```
cd clients\web-panel
npm ci
npm run build
cd ..\..
```

## 5. Put cmcoder into the extension and build it

```
# a fresh copy (an old one from an earlier build would be packed otherwise)
Remove-Item -Recurse -Force visualstudio\src\Cmcoder.VisualStudio\bin\cmcoder -ErrorAction SilentlyContinue
Copy-Item -Recurse dist\cmcoder visualstudio\src\Cmcoder.VisualStudio\bin\cmcoder

powershell -ExecutionPolicy Bypass -File visualstudio\build-vsix.ps1
```

The script finds Visual Studio's MSBuild, builds the Release configuration,
and checks the file holds the chat page, the extension and its manifest. Its
last lines are:

```
Built ...\bin\Release\Cmcoder.VisualStudio.vsix (… files; cmcoder inside: True)
```

**`cmcoder inside` must be `True`.** If it says `False`, step 5's copy was
missed.

Give the file its release name:

```
New-Item -ItemType Directory -Force dist | Out-Null
Copy-Item visualstudio\src\Cmcoder.VisualStudio\bin\Release\Cmcoder.VisualStudio.vsix dist\cmcoder-visualstudio-win32-x64.vsix
Get-FileHash dist\cmcoder-visualstudio-win32-x64.vsix -Algorithm SHA256
```

(`build-vsix.ps1 -Gate` builds the test build the release gate uses. Never
hand that one out.)

## 6. Check it before handing it out

On a test PC (or VM) with Visual Studio 2022 and no Python:

1. Install the file (section 8).
2. Open a solution or folder, then **Tools → cmcoder → Open Chat**, and ask
   something. It answers.
3. **Tools → cmcoder → Copy Diagnostics** shows the bundled program and its
   version, and no API key.

Optional, on the build machine (needs the .NET 8 SDK and a Python with
cmcoder for the mock model server):
`dotnet test visualstudio/test/Cmcoder.Core.Tests` with `CMCODER_TEST_PYTHON`
and `CMCODER_TEST_BINARY` set ([../../visualstudio/README.md](../../visualstudio/README.md)).

## 7. Release it

- **With the other packages:** the Release build builds this file on
  Windows, installs it into a real Visual Studio and runs a whole
  conversation through it with no Python on the PC. It puts the file in the
  bundle's `visualstudio/` folder ([release.md](release.md)).
- **On its own:** hand out `dist\cmcoder-visualstudio-win32-x64.vsix` with
  its SHA256.

The extension isn't code-signed until your company has a certificate, so
Visual Studio's installer shows it as unsigned. With a certificate, sign the
`.vsix` (for example with `OpenVsixSignTool`) before handing it out.

Version: `source.extension.vsixmanifest` and `Properties/AssemblyInfo.cs`
must match `pyproject.toml` (`tests/test_versions.py` checks them). Raise all
three for a new release, or Visual Studio won't offer it as an update.

## 8. On developers' PCs: install

**They need:**
- **Visual Studio 2022, 17.10 or newer** (64-bit; Community, Professional or
  Enterprise);
- the **Microsoft Edge WebView2 Runtime** (part of Windows 11 and of Microsoft
  Edge; without it the chat says what to install);
- **Git for Windows** (shell commands run in Git Bash);
- network access to the AI gateway.

**Install:**
1. Close Visual Studio.
2. Double-click `cmcoder-visualstudio-win32-x64.vsix` and choose
   **Install**. It's unsigned until the company signs it: confirm.
3. Start Visual Studio, open a solution or a folder, then **Tools → cmcoder
   → Open Chat** (or **Ctrl+Shift+Alt+C**).
4. First time only, unless your company's settings came with the build: run
   `"%USERPROFILE%\.cmcoder\bin\cmcoder.exe" setup` in a terminal
   ([../guides/setup.md](../guides/setup.md)).

**Update:** close Visual Studio and install the new file the same way.

**Remove:** **Extensions → Manage Extensions → Installed →** cmcoder **→
Uninstall**, then close Visual Studio to finish. Settings, keys and
conversations stay in `%USERPROFILE%\.cmcoder`.

**For IT (scripted installs):** Visual Studio's `VSIXInstaller.exe` (in
`Common7\IDE` of the Visual Studio folder) installs it quietly:
`VSIXInstaller.exe /quiet cmcoder-visualstudio-win32-x64.vsix`.

User guide: [../guides/visualstudio.md](../guides/visualstudio.md).

## If the build fails

| Problem | Fix |
|---|---|
| "No Visual Studio on this PC", or "Visual Studio's installer is on this PC, but ..." | the message lists what's installed and why each doesn't do (often: Visual Studio's installer came with another product, such as SQL Server Management Studio, but no Visual Studio is installed). Install Visual Studio 2022 or newer, any edition, with the "Visual Studio extension development" workload; or the Build Tools for Visual Studio with "MSBuild" and the ".NET Framework 4.8 targeting pack". A preview (Preview, Insiders) is used when there's no release. `visualstudio\find-msbuild.ps1` runs the same check on its own |
| "Found MSBuild ..., but not the .NET Framework 4.8 targeting pack" | Visual Studio Installer → Modify → Individual components → ".NET Framework 4.8 targeting pack" |
| "panel/chat.js is missing from the .vsix" | build the chat page (step 4) |
| `cmcoder inside: False` | copy `dist\cmcoder` into `visualstudio\src\Cmcoder.VisualStudio\bin\cmcoder` (step 5) |
| NuGet restore errors | the build machine needs nuget.org (or your company's NuGet mirror) |
| Scripts blocked | run them through `powershell -ExecutionPolicy Bypass -File …` as shown |
