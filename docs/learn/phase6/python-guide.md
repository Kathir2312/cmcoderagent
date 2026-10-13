# cmcoder Phase 6, explained for Python developers

Phase 6 is mostly Java, C# and TypeScript ([ide-guide.md](ide-guide.md)).
The Python side is small; this guide covers it.

## 1. No Python on developers' PCs

*Code:* `packaging/build.py` (PyInstaller), `packaging/no_python.py`.
*Tests:* `tests/test_standalone.py`, `tests/test_no_python.py`.

- The standalone `cmcoder` is a folder built by PyInstaller
  (`uv run --with pyinstaller python packaging/build.py`). It's a folder, not
  one file, because it starts faster and antivirus software dislikes
  self-unpacking programs.
- `no_python.py run -- <command>` runs a command with every Python, `py`
  and `uv` removed from `PATH` and `PYTHON*` variables unset. Any Python that
  still starts exits with code 97. The release build runs every test and gate
  under it, so a hidden need for Python fails the build, not a developer.
- `chroma_needs_url()` (`rag/stores.py`): Chroma is reached by URL only
  (DESIGN D17), because the standalone program can't carry `chromadb`.

## 2. Which IDE is calling: `--client`

`cmcoder --protocol stdio --client netbeans` (also `vscode`, `eclipse`,
`visualstudio`) labels the session for usage counts when telemetry is on
(`cli/main.py`, `CLIENTS`). It's accepted only with `--protocol`.

## 3. The terminal package

*Code:* `packaging/terminal.py`, `packaging/terminal/` (install and uninstall
scripts). *Tests:* `tests/test_terminal_package.py`.

A zip of the standalone folder with scripts that install it for the user
(no admin rights) and put it on the user's `PATH`. The test builds the zip,
then installs, updates, runs and uninstalls it, as a developer would.

## 4. The release bundle

*Code:* `packaging/bundle.py`, `packaging/osv_check.py`. *Tests:*
`tests/test_bundle.py`, `tests/test_versions.py`.

- `bundle.py` copies the build's artifacts into folders by IDE and writes
  `README-FIRST.txt`, `GATE-REPORT.md` and `SHA256SUMS.txt`. It refuses to
  make a bundle if any file is missing.
- `GATE-REPORT.md` is built from `gate-<target>.txt`: each platform's build
  job lists the checks it ran, and a job only gets there if all of them
  passed.
- `test_versions.py` reads every manifest (VS Code's `package.json`, the
  Eclipse bundle and feature, the VSIX manifest, the NetBeans `pom.xml`, …)
  and compares each with `pyproject.toml`.
- `osv_check.py` asks OSV about OpenJFX and the NuGet packages the plugins
  ship (CI's security job).

## 5. The mock model server, now for every IDE

`python -m cmcoder.testing.mock_server --script replies.json --port 0`
prints `mock server on <url>`. It answers chat requests with the scripted
replies in order: a text, or tool calls such as `Write` or `getDiagnostics`.
The gates of all four IDEs use it, so each runs the same conversation.
