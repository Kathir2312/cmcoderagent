# The NetBeans plugin: build, release, install

**What you make:** `cmcoder-netbeans-<platform>.nbm`, one file per
platform. Each holds:
- the plugin;
- the browser its chat needs (OpenJFX for that platform: NetBeans has no
  browser of its own);
- the standalone `cmcoder`.

Developers install one file. They need no Python and no JavaFX.

| Platform | File | `-Dplatform=` |
|---|---|---|
| Windows x64 | `cmcoder-netbeans-win32-x64.nbm` | `win` |
| macOS, Apple silicon | `cmcoder-netbeans-darwin-arm64.nbm` | `mac-aarch64` |
| Linux x64 | `cmcoder-netbeans-linux-x64.nbm` | `linux` |

Build each platform's file on that platform: its cmcoder is built there.
The plugin declares its operating system, so NetBeans won't enable the wrong
platform's file.

## 1. The build machine

| Needs | Why |
|---|---|
| Windows 10/11 x64, macOS on Apple silicon, or Linux x64 | the platform you build for |
| **JDK 21** (for example Eclipse Temurin) | Maven builds the plugin with it |
| **Maven 3.9 or newer** | the build |
| **Git** (on Windows: Git for Windows) | the build is named after its commit |
| **uv** ([docs.astral.sh/uv](https://docs.astral.sh/uv/)) | builds cmcoder; it downloads its own Python 3.12 |
| **Node.js 22 LTS** (with npm) | builds the shared chat page |
| Internet access to PyPI, npm and Maven Central | the NetBeans APIs, OpenJFX and the build's packages |

Check the tools:

```
java -version      # 21
mvn -v             # 3.9.x or newer, using Java 21
node --version     # v22.x
```

## 2. Get the source

```
git clone <your repository URL> cmcoderagent
cd cmcoderagent
git checkout <the branch or tag to build>
```

Use a git clone (the build is named after its commit). Put your company's
details in `branding/` first
([README.md](README.md#before-the-first-build-your-companys-details)).

## 3. Build cmcoder and check it without Python

```
uv sync
uv run --with pyinstaller python packaging/build.py
```

Then run the check from [cli.md, section 4](cli.md#4-check-it-runs-without-python).
The output is `dist/cmcoder/`.

## 4. Build the chat page, put cmcoder in, build the plugin

macOS, Linux (use `-Dplatform=mac-aarch64` on a Mac, `linux` on Linux):

```
(cd clients/web-panel && npm ci && npm run build)
rm -rf netbeans/bin && mkdir -p netbeans/bin
cp -RL dist/cmcoder netbeans/bin/cmcoder          # -L: links as files
(cd netbeans && mvn -B package -Dplatform=linux)
mkdir -p dist && cp netbeans/target/cmcoder-netbeans-linux-x64.nbm dist/
```

Windows (PowerShell):

```
cd clients\web-panel; npm ci; npm run build; cd ..\..
Remove-Item -Recurse -Force netbeans\bin -ErrorAction SilentlyContinue
New-Item -ItemType Directory netbeans\bin | Out-Null
Copy-Item -Recurse dist\cmcoder netbeans\bin\cmcoder
cd netbeans; mvn -B package -Dplatform=win; cd ..
Copy-Item netbeans\target\cmcoder-netbeans-win32-x64.nbm dist\
```

The output is `netbeans/target/cmcoder-netbeans-<platform>.nbm`, already
named for release.

(`-Pgate` builds the test build the release gate uses. Never hand that one
out.)

## 5. Check it before handing it out

1. cmcoder is inside (an `.nbm` is a zip):

   ```
   unzip -l dist/cmcoder-netbeans-linux-x64.nbm | grep "bin/cmcoder/cmcoder"           # macOS, Linux
   tar -tf dist\cmcoder-netbeans-win32-x64.nbm | findstr "bin/cmcoder/cmcoder.exe"     # Windows
   ```

2. On a test PC without Python, install it (section 7), open a project,
   **Tools → cmcoder → Open cmcoder Chat**, and ask something. Then use
   **Tools → cmcoder → Copy cmcoder Diagnostics**: it shows the bundled
   program and no API key.

## 6. Release it

- **With the other packages:** the Release build builds all three files,
  installs each into a fresh NetBeans 31 and runs a whole conversation with
  the bundled cmcoder and no Python. The files go in the bundle's
  `netbeans/` folder ([release.md](release.md)).
- **On its own:** hand out `dist/cmcoder-netbeans-<platform>.nbm` with its
  SHA256.

The plugin isn't signed until your company has a certificate: NetBeans
warns during the install.

Version: `netbeans/pom.xml`'s version must match `pyproject.toml`
(`tests/test_versions.py` checks it). Raise both for a new release, or
NetBeans won't treat it as an update.

## 7. On developers' PCs: install

**They need:**
- **Apache NetBeans 28 or newer**, running on **JDK 17 or newer** (21 or 25
  recommended);
- on Windows, **Git for Windows**;
- on Linux, **GTK 3** (`libgtk-3-0`; desktop Linux has it);
- network access to the AI gateway.

**Install:**
1. **Tools → Plugins**, then the **Downloaded** tab.
2. **Add Plugins…**, choose the `.nbm` for their computer, then **Install**.
3. **Next**, accept the license, **Install**.
4. NetBeans warns that the plugin isn't signed: **Continue**.
5. **Restart IDE Now**.
6. **Tools → cmcoder → Open cmcoder Chat**, or **Ctrl+Alt+J** (⌘⌥J).
7. First time only, unless your company's settings came with the build:
   `cmcoder setup` ([../guides/setup.md](../guides/setup.md)).

**Update:** install the new `.nbm` the same way.

**Remove:** **Tools → Plugins → Installed →** cmcoder **→ Uninstall**, then
restart. Settings, keys and conversations stay in `~/.cmcoder`.

User guide: [../guides/netbeans.md](../guides/netbeans.md).

## If the build fails

| Problem | Fix |
|---|---|
| "Unresolveable build extension: nbm-maven-plugin" / HTTP 429 | Maven Central refused the download (rate limit or proxy): wait and run again, or use your company's Maven mirror (`~/.m2/settings.xml`) |
| Maven says the Java version is wrong | `mvn -v` must show Java 17 or newer (21 recommended): set `JAVA_HOME` |
| The chat page is missing | build `clients/web-panel` first (step 4) |
| cmcoder isn't in the `.nbm` | `netbeans/bin/cmcoder` was empty: copy `dist/cmcoder` there (step 4) |
| "cannot be installed" on a developer's PC | NetBeans older than 28, or another platform's file |
