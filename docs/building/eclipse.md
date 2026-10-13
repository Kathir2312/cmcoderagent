# The Eclipse plugin: build, release, install

**What you make:** `cmcoder-eclipse.zip`, an Eclipse update-site archive.
It is **one file for Windows, macOS and Linux**: it holds the plugin and one
fragment per platform, each with that platform's standalone `cmcoder`.
Eclipse installs only the developer's platform. Developers need no Python.

Because the archive holds all three platforms' cmcoder, you build cmcoder
three times (each on its own platform), then build the archive once.

## 1. The build machine

| Needs | Why |
|---|---|
| Windows, macOS or Linux (Linux or macOS is easiest; see step 4) | builds the archive |
| **JDK 21** (for example Eclipse Temurin) | Maven and Tycho build the plugin with it |
| **Maven 3.9 or newer** | the build (Tycho 5) |
| **Node.js 22 LTS** (with npm) | builds the shared chat page |
| **Git** | the source |
| Internet access to Maven Central and `download.eclipse.org` | Tycho downloads the Eclipse it builds against |
| The three platforms' cmcoder | step 3 |

To build cmcoder itself (step 3), each platform's machine also needs **uv**
([docs.astral.sh/uv](https://docs.astral.sh/uv/)) and Git, as in
[cli.md](cli.md#1-the-build-machine).

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

Put your company's details in `branding/` first
([README.md](README.md#before-the-first-build-your-companys-details)).

## 3. Get cmcoder for each platform

On each platform's machine (Windows x64, an Apple silicon Mac, Linux x64), in
a clone of the **same commit**:

```
uv sync
uv run --with pyinstaller python packaging/build.py
```

Check each without Python as in [cli.md](cli.md#4-check-it-runs-without-python).
Then copy each machine's `dist/cmcoder` folder to the build machine. Keep
the folder whole, and keep the macOS and Linux files as they are (copy a
`.tar.gz` or `.zip` of the folder, not loose files).

Alternatively, take them from a green **Release build** run (**Actions →
Release build → the run → Artifacts**): `cmcoder-win32-x64`,
`cmcoder-darwin-arm64` and `cmcoder-linux-x64` are these folders. GitHub
wraps each in a `.zip`: unzip each into a folder of its own (the zip drops
the executable mark; step 4's `chmod` puts it back).

## 4. Put each cmcoder into its fragment

The fragments are named after Eclipse's platform names:

| Platform | Fragment folder |
|---|---|
| Windows x64 | `eclipse/fragments/cmcoder.eclipse.win32.x86_64/bin/cmcoder` |
| macOS, Apple silicon | `eclipse/fragments/cmcoder.eclipse.macosx.aarch64/bin/cmcoder` |
| Linux x64 | `eclipse/fragments/cmcoder.eclipse.linux.x86_64/bin/cmcoder` |

On macOS or Linux (replace the source paths with where you put the folders):

```
rm -rf eclipse/fragments/*/bin/cmcoder            # no copy from an earlier build
cp -RL /path/to/cmcoder-win32-x64     eclipse/fragments/cmcoder.eclipse.win32.x86_64/bin/cmcoder
cp -RL /path/to/cmcoder-darwin-arm64  eclipse/fragments/cmcoder.eclipse.macosx.aarch64/bin/cmcoder
cp -RL /path/to/cmcoder-linux-x64     eclipse/fragments/cmcoder.eclipse.linux.x86_64/bin/cmcoder
chmod +x eclipse/fragments/*/bin/cmcoder/cmcoder
```

`-L` copies links as files (Eclipse's installer doesn't keep links).

On Windows (PowerShell):

```
Get-ChildItem eclipse\fragments\*\bin\cmcoder -Directory -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force
Copy-Item -Recurse C:\path\to\cmcoder-win32-x64    eclipse\fragments\cmcoder.eclipse.win32.x86_64\bin\cmcoder
Copy-Item -Recurse C:\path\to\cmcoder-darwin-arm64 eclipse\fragments\cmcoder.eclipse.macosx.aarch64\bin\cmcoder
Copy-Item -Recurse C:\path\to\cmcoder-linux-x64    eclipse\fragments\cmcoder.eclipse.linux.x86_64\bin\cmcoder
```

Windows can't mark files executable for macOS and Linux. The plugin sets the
mark itself the first time it starts there, so a Windows-built archive works
too.

**One platform only (for a try-out):** fill only your platform's fragment. On
the other platforms the plugin then looks for `cmcoder` on PATH. Don't hand
such an archive out.

## 5. Build the chat page and the archive

```
cd clients/web-panel
npm ci
npm run build
cd ../../eclipse
mvn -B -DskipTests verify
cd ..
```

The first build downloads Eclipse 2024-06 to build against, so it takes a few
minutes. The output is `eclipse/site/target/cmcoder-eclipse-<version>.zip`.

`-DskipTests` skips the gate test: it starts a real Eclipse and needs a
model server. The Release build runs it on every platform.

Give the file its release name:

```
mkdir -p dist && cp eclipse/site/target/cmcoder-eclipse-*.zip dist/cmcoder-eclipse.zip     # macOS, Linux
Copy-Item (Get-Item eclipse\site\target\cmcoder-eclipse-*.zip) dist\cmcoder-eclipse.zip      # Windows
```

## 6. Check it before handing it out

1. Every platform's cmcoder is in the archive (macOS, Linux):

   ```
   mkdir /tmp/site && cd /tmp/site && unzip -q <repo>/dist/cmcoder-eclipse.zip
   for f in win32.x86_64:cmcoder.exe linux.x86_64:cmcoder macosx.aarch64:cmcoder; do
     jar=$(ls plugins/cmcoder.eclipse.${f%%:*}_*.jar)
     unzip -l "$jar" | grep -q " bin/cmcoder/${f##*:}$" && echo "ok ${f%%:*}" || echo "MISSING ${f%%:*}"
   done
   ```

2. On a test PC without Python, install it (section 8), open a project, open
   the cmcoder view and ask something. Then run **cmcoder: Copy
   Diagnostics** (Ctrl+3): it shows the bundled program and no API key.

Afterwards, empty the fragments (`rm -rf eclipse/fragments/*/bin/cmcoder`).
git ignores them, but an old copy left there would go into the next build.

## 7. Release it

- **With the other packages:** the Release build builds cmcoder on all three
  platforms, runs the plugin in a real Eclipse on each with its bundled
  cmcoder and no Python, then builds this archive from the three. It goes
  in the bundle's `eclipse/` folder ([release.md](release.md)).
- **On its own:** hand out `dist/cmcoder-eclipse.zip` with its SHA256
  (`shasum -a 256` / `Get-FileHash`).

The plugin isn't signed until your company has a certificate: Eclipse asks
developers to trust unsigned content during the install.

Version: the plugin's `MANIFEST.MF`, the feature and the Maven files must
match `pyproject.toml` (`tests/test_versions.py` checks them). Raise them all
for a new release, or Eclipse won't see an update.

## 8. On developers' PCs: install

**They need:**
- **Eclipse 2024-06 or newer**, running on Java 21 (the Eclipse packages
  include it);
- on Windows, the **Microsoft Edge WebView2 Runtime** (part of Windows 11 and
  Edge) and **Git for Windows**;
- on Linux, **WebKitGTK** (`sudo apt install libwebkit2gtk-4.1-0`);
- network access to the AI gateway.

**Install:**
1. **Help → Install New Software… → Add… → Archive…** and choose
   `cmcoder-eclipse.zip` (keep it zipped).
2. Tick **cmcoder**, **Next**, accept the license, **Finish**.
3. When Eclipse asks whether to trust the unsigned content: trust it and
   go on (it is unsigned until your company signs it).
4. **Restart Eclipse**.
5. **Window → Show View → Other… → cmcoder**, or **Ctrl+Alt+J** (⌘⌥J).
6. First time only, unless your company's settings came with the build:
   `cmcoder setup` ([../guides/setup.md](../guides/setup.md)).

**Update:** install the new archive the same way (Eclipse offers it as an
update), and restart.

**Remove:** **Help → About Eclipse IDE → Installation Details →** cmcoder
**→ Uninstall…**, then restart. Settings, keys and conversations stay in
`~/.cmcoder`.

User guide: [../guides/eclipse.md](../guides/eclipse.md).

## If the build fails

| Problem | Fix |
|---|---|
| "Build the shared chat panel first" | step 5's `npm ci` and `npm run build` in `clients/web-panel` |
| Tycho can't resolve the target platform | the build machine needs `download.eclipse.org` (or your company's Eclipse mirror, in `eclipse/target-platform/*.target`) |
| Maven says the Java version is wrong | `mvn -v` must show Java 21: set `JAVA_HOME` |
| A platform's cmcoder is missing from the archive | its fragment's `bin/cmcoder` was empty (step 4) |
