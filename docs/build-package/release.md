# Releasing: the release bundle

A release is one folder (the **release bundle**) with every package for every
platform, one version number, a gate report and checksums. You hand that
folder out, or the files in it. Developers need no Python.

```
README-FIRST.txt     which file for which IDE, and the two-line install
GATE-REPORT.md       what was tested, per platform, and what wasn't
SHA256SUMS.txt       checksums of every file
terminal/            cmcoder-terminal-<platform>.zip         (cli.md)
vscode/              cmcoder-<platform>.vsix                 (vscode.md)
visualstudio/        cmcoder-visualstudio-win32-x64.vsix     (visualstudio.md)
eclipse/             cmcoder-eclipse.zip, all platforms      (eclipse.md)
netbeans/            cmcoder-netbeans-<platform>.nbm         (netbeans.md)
jetbrains/           cmcoder-jetbrains-<platform>.zip        (intellij.md)
admin/               managed settings and the scripts that install them (for IT)
```

`<platform>` is `win32-x64`, `darwin-arm64` or `linux-x64`.

There are two ways to make it. **A is the normal way.** Use B only where
GitHub Actions can't be used.

## A. With the Release build (GitHub Actions)

GitHub's own Windows, macOS and Linux machines build everything. You need no
build machine. On each platform the workflow:
- builds cmcoder and tests it with no Python;
- builds every package around it;
- runs each package in the real IDE with no Python on the PC (the gates).

The bundle is made **only if every check passed**.

### Before the first release

- The repository is on GitHub with Actions enabled.
- `branding/` holds your company's details, committed
  ([README.md](README.md#before-the-first-build-your-companys-details)).

### Every release

1. **Set the version** (for example `0.2.0`) in every file that carries it:

   | File | What to change |
   |---|---|
   | `pyproject.toml`, `src/cmcoder/__init__.py` | `version`, `__version__` |
   | `vscode/package.json` (and `package-lock.json`) | run `npm version 0.2.0 --no-git-tag-version` in `vscode/` |
   | `clients/jvm-core/pom.xml`, `netbeans/pom.xml` | `<version>` |
   | `eclipse/` (poms, manifests, feature) | run `mvn -B org.eclipse.tycho:tycho-versions-plugin:set-version -DnewVersion=0.2.0-SNAPSHOT` in `eclipse/`, then check the fragments' `Fragment-Host` range (`[0.2.0,0.3.0)`) |
   | `visualstudio/src/Cmcoder.VisualStudio/source.extension.vsixmanifest`, `Properties/AssemblyInfo.cs` | `Version="0.2.0"`, `0.2.0.0` |
   | `jetbrains/gradle.properties` | `pluginVersion = 0.2.0` |

   Then `uv lock` (the lock file names the version) and check:

   ```
   uv run pytest -q tests/test_versions.py
   ```

   It lists every file whose version doesn't match `pyproject.toml`. The
   IDEs offer an update only when the version is higher.

2. **Commit, tag and push:**

   ```
   git commit -am "Release 0.2.0"
   git tag v0.2.0
   git push origin <branch> v0.2.0
   ```

   (To build without releasing, use **Actions → Release build → Run
   workflow** instead of a tag.)

3. **Wait for the Release build** (**Actions → Release build**, about 40
   minutes). The jobs are: `build (win32-x64)`, `build (darwin-arm64)`,
   `build (linux-x64)`, `Eclipse update site`, `Release bundle`. If one
   fails, its log says which check and why. Fix it, then tag again.

4. **Take the bundle.**
   - With a tag: the workflow makes a GitHub release named after it, with
     every file and `GATE-REPORT.md` as its notes.
   - Otherwise: the run's page → **Artifacts** → `cmcoder-release-bundle`
     (GitHub wraps it in a `.zip`: unzip it once).

5. **Read `GATE-REPORT.md`.** One row per check and one column per platform:
   ✅ passed; — means not run there (Visual Studio exists only on Windows, for
   example). The "Not covered" list says what a person should still know:
   signing, a real gateway, older IDE versions on some platforms.

6. **Hand it out** (section C).

## B. By hand, on your own build machines

You need one build machine per platform: Windows x64, an Apple silicon Mac
and Linux x64. You also need all the tools from the package pages.

1. On each platform, build that platform's packages:
   `packaging\package-all.ps1` on Windows, `bash packaging/package-all.sh`
   on macOS and Linux ([README.md](README.md#one-command-per-package)), or
   one by one following each page: [cli.md](cli.md), [vscode.md](vscode.md),
   [netbeans.md](netbeans.md) and [intellij.md](intellij.md); on Windows also
   [visualstudio.md](visualstudio.md). Install each one on a test PC without
   Python, as each page's check section says.
2. Build [eclipse.md](eclipse.md) once, from the three platforms' cmcoder
   (`packaging/package-eclipse.sh` or `.ps1`).
3. Collect the files in this layout (the folder names are what
   `packaging/bundle.py` reads). Here for Windows; the same for
   `darwin-arm64` and `linux-x64`:

   ```
   artifacts/
     cmcoder-terminal-win32-x64/cmcoder-win32-x64.zip          (dist/terminal/…)
     cmcoder-win32-x64.vsix/cmcoder-win32-x64.vsix             (vscode/…)
     cmcoder-netbeans-win32-x64.nbm/cmcoder-netbeans-win32-x64.nbm     (dist/…)
     cmcoder-jetbrains-win32-x64.zip/cmcoder-jetbrains-win32-x64.zip   (dist/…)
     gate-win32-x64/gate-win32-x64.txt                          (see below)
     cmcoder-visualstudio.vsix/cmcoder-visualstudio.vsix       (Windows only; dist/cmcoder-visualstudio-win32-x64.vsix, renamed)
     cmcoder-eclipse/cmcoder-eclipse-0.2.0.zip                  (once; eclipse/site/target/…)
   ```

   `gate-<platform>.txt` lists, one per line, what you checked on that
   platform, for example `VS Code extension: installed on a test PC without
   Python, chat answered`. These lines become the gate report's rows, so
   write only what you really checked.

4. Make the bundle:

   ```
   uv run python packaging/bundle.py --artifacts artifacts --out bundle
   ```

   It refuses to make a bundle if any file is missing. It writes
   `README-FIRST.txt`, `GATE-REPORT.md`, `SHA256SUMS.txt` and `admin/`.

## C. Hand it out

- Put the bundle on a share, the intranet, or the GitHub release, with the
  user guides ([../guides/README.md](../guides/README.md)).
- Developers pick their IDE's file and follow its guide. Every package
  installs without administrator rights, and the IDEs install them per user.
- Developers can check a file against `SHA256SUMS.txt`:

  ```
  Get-FileHash .\vscode\cmcoder-win32-x64.vsix -Algorithm SHA256     # Windows
  shasum -a 256 -c SHA256SUMS.txt                                   # macOS, Linux (in the bundle)
  ```

- **IT** installs the managed settings (rules developers can't change) from
  `admin/` ([../admin/](../admin/README.md), section 5).
- **First-time setup** per developer: nothing, if `branding/settings.template.json`
  was set; otherwise `cmcoder setup` ([../guides/setup.md](../guides/setup.md)).

### Updates

A new release is a new bundle. Developers install the new file over the old
one, the same way they installed it. Every IDE replaces its plugin, and the
shared `cmcoder` in `.cmcoder/bin` is updated by whichever newer package
starts first. Settings, API keys and saved conversations are kept.

### Signing

The files aren't code-signed until your company has a certificate. Until
then, Windows SmartScreen, antivirus and macOS Gatekeeper may warn, and the
IDEs show the plugins as unsigned. Tell developers, or sign before handing
out: `cmcoder.exe` with `signtool`, the macOS program with `codesign` (and
notarize it), and the IDE files with each IDE's signing tool.
