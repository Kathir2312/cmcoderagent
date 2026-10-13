# Building, packing and releasing cmcoder

These pages are for the people who **build** cmcoder and hand it out. Each
page covers one package: what the build machine needs, how to build and pack
it, how to check and release it, and how developers install it.

| Package | Developers use it in | Page |
|---|---|---|
| Terminal package (the standalone `cmcoder` CLI) | PowerShell, cmd, Terminal, bash | [cli.md](cli.md) |
| VS Code extension | VS Code 1.90+ | [vscode.md](vscode.md) |
| Visual Studio extension | Visual Studio 2022 (17.10+) | [visualstudio.md](visualstudio.md) |
| Eclipse plugin | Eclipse 2024-06+ | [eclipse.md](eclipse.md) |
| NetBeans plugin | Apache NetBeans 28+ | [netbeans.md](netbeans.md) |
| JetBrains plugin | IntelliJ IDEA, PyCharm, Rider, WebStorm, GoLand, … 2024.2+ | [intellij.md](intellij.md) |
| The release bundle (all of the above, checked and with checksums) | — | [release.md](release.md) |

## Two kinds of machine

- **The build machine** has the tools: Git, uv (which brings its own Python),
  Node.js, and per IDE a JDK, Maven, Gradle or Visual Studio. Only the people
  who build need one.
- **Developers' PCs have no Python**, and need none: no Python, pip, uv or
  Node.js. Every package carries its own `cmcoder` (a standalone program built
  with PyInstaller). The packages install without administrator rights.

## How every package is made

```
 your repository (git clone) + branding/
        │
        ▼
 1. the standalone cmcoder ──► dist/cmcoder/          (one per platform)
        │
        ├─► terminal package  (cmcoder + install scripts)
        ├─► VS Code .vsix      (cmcoder inside)
        ├─► Visual Studio .vsix (cmcoder inside, Windows only)
        ├─► Eclipse .zip       (all three platforms' cmcoder inside)
        ├─► NetBeans .nbm      (cmcoder + its chat browser inside)
        └─► JetBrains .zip     (cmcoder inside)
```

Step 1 is the same for every package; each page repeats it so you can read
the page alone.

**Build each platform's files on that platform.** PyInstaller can't build
for another operating system, so `cmcoder` for Windows is built on Windows,
for macOS (Apple silicon) on an Apple silicon Mac, and for Linux on Linux
(x64). The files are named after the platform:

| Platform | Name in file names |
|---|---|
| Windows x64 | `win32-x64` |
| macOS, Apple silicon | `darwin-arm64` |
| Linux x64 | `linux-x64` |

## Two ways to build

**A. The Release build in GitHub Actions (recommended for releases).** One
push builds every package on Windows, macOS and Linux. It runs each one in
the real IDE with no Python on the PC (the "gates"), and collects them into
one bundle with a gate report and checksums. You need no build machine of
your own. See [release.md](release.md).

**B. On your own build machine.** Use this to build one package for one
platform, for example to try a branding change or to build inside a network
that GitHub can't reach. Each package has a one-command script (below); the
package's page lists what the build machine needs and the same steps by
hand. Before handing the file out, install it on a test PC without Python
(each page says how).

## One command per package

Run these in the repository (a git clone), on the platform you build for.
Each does what the Release build does for its file: builds the standalone
cmcoder, runs it with no Python in reach, builds the chat page, makes the
package with cmcoder inside, checks the program really is in the file, and
prints the file's path and SHA256.

| Package | Windows (PowerShell) | macOS, Linux | Makes |
|---|---|---|---|
| Terminal | `packaging\package-terminal.ps1` | `packaging/package-terminal.sh` | `dist/cmcoder-terminal-<platform>.zip` |
| VS Code | `packaging\package-vscode.ps1` | `packaging/package-vscode.sh` | `vscode/cmcoder-<platform>.vsix` |
| Visual Studio | `packaging\package-visualstudio.ps1` | — | `dist\cmcoder-visualstudio-win32-x64.vsix` |
| NetBeans | `packaging\package-netbeans.ps1` | `packaging/package-netbeans.sh` | `dist/cmcoder-netbeans-<platform>.nbm` |
| JetBrains | `packaging\package-jetbrains.ps1` | `packaging/package-jetbrains.sh` | `dist/cmcoder-jetbrains-<platform>.zip` |
| Eclipse (all platforms in one file) | `packaging\package-eclipse.ps1` | `packaging/package-eclipse.sh` | `dist/cmcoder-eclipse.zip` |
| **Every package above but Eclipse** | `packaging\package-all.ps1` | `packaging/package-all.sh` | all of them, cmcoder built once |

Run the `.ps1` scripts with `powershell -ExecutionPolicy Bypass -File …`
and the `.sh` scripts with `bash …`. What each one needs is at the top of
the script (`--help` prints it for the `.sh` ones). The options:

| Windows / macOS, Linux | What it does |
|---|---|
| `-SkipTests` / `--skip-tests` | leaves out the test runs. Only for try-outs: a release always passes them |
| `-UseBuilt` / `--use-built` | uses `dist/cmcoder` from an earlier run instead of building it again |
| `-Only terminal,vscode` / `--only terminal,vscode` | `package-all` only: just these packages, for a build machine that lacks some packages' tools |

The Eclipse script also takes the other platforms' cmcoder: see
[eclipse.md](eclipse.md#the-quick-way-one-command).

For example, everything for Windows on a build PC with all the tools:

```
powershell -ExecutionPolicy Bypass -File packaging\package-all.ps1
```

or, on a Mac without Gradle:

```
bash packaging/package-all.sh --only terminal,vscode,netbeans
```

## Before the first build: your company's details

| What | Where |
|---|---|
| Product name, icon, publisher | `branding/brand.json`, `branding/icon.png`, `branding/icon-mono.svg` ([../phase4/branding.md](../phase4/branding.md)) |
| Your gateway and models, pre-filled for every developer (optional) | `branding/settings.template.json` |
| Rules developers can't change (optional) | `branding/managed-settings.json` (installed by IT; [../guides/admin.md](../guides/admin.md), section 5) |

Every package built afterwards carries them. The build stops on a template or
managed file that isn't valid, or that still has an example address.

## Common build-machine requirements

Each page lists exactly what its package needs. In short:

| Tool | Version | Needed for |
|---|---|---|
| Git | any recent | every package: the build is named after its commit, so build from a `git clone` (not a downloaded zip) |
| uv | any recent ([docs.astral.sh/uv](https://docs.astral.sh/uv/)) | every package: builds `cmcoder`; it downloads its own Python 3.12, so Python needn't be installed |
| Node.js | 22 LTS | every IDE package (the shared chat page) |
| JDK | 21 (e.g. Eclipse Temurin) | Eclipse, NetBeans, JetBrains |
| Maven | 3.9 or newer | Eclipse, NetBeans |
| Gradle | 9.8.1 | JetBrains |
| Visual Studio 2022 | 17.10+, with the "Visual Studio extension development" workload | Visual Studio (Windows only) |

The build downloads packages from the internet: PyPI and npm for every
package, Maven Central for the Java ones, and also `download.eclipse.org`
(Eclipse) or JetBrains' servers (JetBrains). Behind a proxy, set
`HTTPS_PROXY` (and Maven's or Gradle's proxy settings) on the build machine.
