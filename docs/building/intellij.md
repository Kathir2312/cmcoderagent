# The JetBrains plugin (IntelliJ IDEA and the others): build, release, install

**What you make:** `cmcoder-jetbrains-<platform>.zip`, one plugin file per
platform with the standalone `cmcoder` inside. The same file works in
**IntelliJ IDEA** (Community or Ultimate), **PyCharm**, **Rider**,
**WebStorm**, **GoLand**, **PhpStorm**, **CLion**, **RubyMine** and
**Android Studio**, 2024.2 or newer. Developers install it from disk and
need no Python. The chat uses the IDE's built-in browser.

| Platform | File |
|---|---|
| Windows x64 | `cmcoder-jetbrains-win32-x64.zip` |
| macOS, Apple silicon | `cmcoder-jetbrains-darwin-arm64.zip` |
| Linux x64 | `cmcoder-jetbrains-linux-x64.zip` |

Build each platform's file on that platform: its cmcoder is built there.

## 1. The build machine

| Needs | Why |
|---|---|
| Windows 10/11 x64, macOS on Apple silicon, or Linux x64 | the platform you build for |
| **JDK 21** (for example Eclipse Temurin) | Gradle builds the plugin with it |
| **Gradle 9.8.1** ([gradle.org/releases](https://gradle.org/releases/); the repository has no Gradle wrapper) | the build (the IntelliJ Platform Gradle Plugin needs Gradle 9) |
| **Git** (on Windows: Git for Windows) | the build is named after its commit |
| **uv** ([docs.astral.sh/uv](https://docs.astral.sh/uv/)) | builds cmcoder; it downloads its own Python 3.12 |
| **Node.js 22 LTS** (with npm) | builds the shared chat page |
| Internet access to PyPI, npm, Maven Central, the Gradle Plugin Portal and JetBrains' servers (`*.jetbrains.com`) | the build downloads the IntelliJ Platform it compiles against |
| About 5 GB free disk | Gradle's cache holds the IntelliJ Platform |

Check the tools:

```
java -version      # 21
gradle --version   # 9.8.1, JVM 21
node --version     # v22.x
```

## 2. Get the source

```
git clone <your repository URL> cmcoderagent
cd cmcoderagent
git checkout <the branch or tag to build>
```

Use a git clone (the build is named after its commit). Put your company's
details in `branding/` first: the plugin's name, vendor, menus and icons
come from it ([README.md](README.md#before-the-first-build-your-companys-details)).

## 3. Build cmcoder and check it without Python

```
uv sync
uv run --with pyinstaller python packaging/build.py
```

Then run the check from [cli.md, section 4](cli.md#4-check-it-runs-without-python).
The output is `dist/cmcoder/`.

## 4. Build the chat page and the plugin, with cmcoder inside

`-PcmcoderDir` names the cmcoder folder to put in the plugin. Use its full
path.

macOS, Linux:

```
(cd clients/web-panel && npm ci && npm run build)
cd jetbrains
gradle --no-daemon "-PcmcoderDir=$(cd ../dist/cmcoder && pwd)" buildPlugin
cd ..
mkdir -p dist && cp jetbrains/build/distributions/cmcoder-jetbrains-*[0-9].zip dist/cmcoder-jetbrains-linux-x64.zip
```

(On a Mac, name it `cmcoder-jetbrains-darwin-arm64.zip`.)

Windows (PowerShell):

```
cd clients\web-panel; npm ci; npm run build; cd ..\..
cd jetbrains
gradle --no-daemon "-PcmcoderDir=$((Resolve-Path ..\dist\cmcoder).Path)" buildPlugin
cd ..
Copy-Item (Get-ChildItem jetbrains\build\distributions\cmcoder-jetbrains-*.zip | Where-Object Name -notlike '*-gate.zip' | Select-Object -First 1) dist\cmcoder-jetbrains-win32-x64.zip
```

The first build downloads the IntelliJ Platform (2024.2.5, about 1 GB),
so it takes a while. The build checks the plugin has its chat page and your
product name. The output is
`jetbrains/build/distributions/cmcoder-jetbrains-<version>.zip`.

(`gradle -Pgate … buildPlugin` makes `…-gate.zip`, the test build the
release gate uses. Never hand that one out.)

Without `-PcmcoderDir` the plugin has no cmcoder inside and looks for one on
the PC: that's for maintainers only.

### Optional: JetBrains' Plugin Verifier

```
cd jetbrains
gradle --no-daemon verifyPlugin
```

This checks the plugin against IntelliJ IDEA 2024.2, the newest IntelliJ
IDEA and the newest Rider. It downloads each IDE (several GB). CI runs it on
every change.

## 5. Check it before handing it out

1. cmcoder is inside:

   ```
   unzip -l dist/cmcoder-jetbrains-linux-x64.zip | grep "bin/cmcoder/cmcoder"           # macOS, Linux
   tar -tf dist\cmcoder-jetbrains-win32-x64.zip | findstr "bin/cmcoder/cmcoder.exe"     # Windows
   ```

2. On a test PC without Python, install it in IntelliJ IDEA (section 7), open
   a project, then **Tools → cmcoder → Open cmcoder Chat**, and ask
   something. Then use **Tools → cmcoder → Copy cmcoder Diagnostics**: it
   shows the bundled program and no API key.

## 6. Release it

- **With the other packages:** the Release build builds all three files and
  runs the gate in the newest IntelliJ IDEA on Windows, macOS and Linux with
  the bundled cmcoder and no Python. CI also runs it in IntelliJ IDEA 2024.2
  and PyCharm. The files go in the bundle's `jetbrains/` folder
  ([release.md](release.md)).
- **On its own:** hand out `dist/cmcoder-jetbrains-<platform>.zip` with its
  SHA256.

The plugin isn't signed until your company has a certificate. Installing
from disk works without a signature; the IDE may show its note about
third-party plugins.

Version: `jetbrains/gradle.properties` (`pluginVersion`) must match
`pyproject.toml` (`tests/test_versions.py` checks it). Raise both for a new
release.

## 7. On developers' PCs: install

**They need:**
- a **JetBrains IDE 2024.2 or newer** (IntelliJ IDEA, PyCharm, Rider,
  WebStorm, GoLand, …);
- on Windows, **Git for Windows**;
- network access to the AI gateway.

Nothing else: the chat uses the IDE's own browser (JCEF).

**Install:**
1. **File → Settings → Plugins** (Mac: **IntelliJ IDEA → Settings… →
   Plugins**; the other IDEs the same, with their name).
2. **⚙ → Install Plugin from Disk…**, then choose
   `cmcoder-jetbrains-<platform>.zip`. Keep it zipped.
3. If the IDE shows its note about third-party plugins: **Accept**.
4. **OK**, and **Restart IDE** if asked.
5. Open a project, then click **cmcoder** on the right edge (or **Tools →
   cmcoder → Open cmcoder Chat**).
6. First time only, unless your company's settings came with the build:
   `cmcoder setup` ([../guides/setup.md](../guides/setup.md)).

Ask about selected code with **Alt+Shift+K** (Mac: ⌥⇧K) or right-click →
**Ask cmcoder About Selection**.

**Update:** install the new zip the same way; the IDE replaces the old
version.

**Remove:** **Settings → Plugins → Installed →** cmcoder **→** the arrow
next to **Disable** **→ Uninstall**, then restart if asked. Settings, keys
and conversations stay in `~/.cmcoder`.

Remote development (JetBrains Gateway, a remote backend) isn't supported:
use the plugin in a local IDE.

User guide: [../guides/intellij.md](../guides/intellij.md).

## If the build fails

| Problem | Fix |
|---|---|
| "IntelliJ Platform Gradle Plugin requires Gradle 9.0.0 and higher" | install Gradle 9.8.1 |
| "No chat page in the plugin" | build `clients/web-panel` first (step 4) |
| Downloads from `*.jetbrains.com` fail | the build machine needs JetBrains' servers (or your company's mirror of the IntelliJ Platform) |
| "Resolved … which is not part of the dependency lock state" | the build's tools changed: run `gradle buildEnvironment dependencies --configuration runtimeClasspath --write-locks` in `jetbrains/` and commit the lock files |
| HTTP 429 from Maven Central | wait and run again, or use your company's Maven mirror |
| cmcoder isn't in the zip | `-PcmcoderDir` was missing or wrong (step 4) |
