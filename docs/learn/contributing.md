# Working on cmcoder: run it, test it, change it

For people changing cmcoder itself. (Using it: [../guides/](../guides/README.md);
building packages for others: [../build-package/](../build-package/README.md).)

## Set up

You need Git, [uv](https://docs.astral.sh/uv/) (it brings Python 3.12) and,
for the IDE parts, Node.js 22 and a JDK 21. On Windows, Git for Windows (cmcoder
runs shell commands with Git Bash). [ripgrep](https://github.com/BurntSushi/ripgrep)
is optional (faster Grep/Glob).

```bash
git clone <your repository URL> cmcoderagent
cd cmcoderagent
uv sync
uv run cmcoder --version
uv run cmcoder doctor          # after ~/.cmcoder/settings.json and `uv run cmcoder login`
```

To install it as a tool from source: `uv tool install git+<your repository URL>`.

No gateway at hand? The mock server answers with scripted replies:
`uv run python -m cmcoder.testing.mock_server --script replies.json`.

## The checks (run them before every commit)

```bash
uv run ruff check src tests evals packaging && uv run ruff format --check src tests evals packaging
uv run pyright
uv run pytest -q                       # unit, CLI, TLS, terminal (pty; not on Windows), docs links
uv run python evals/run.py --mock      # the eval harness with scripted replies
```

The repository's `CMCODER.md` lists them too, so cmcoder runs them when it
works on itself.

More, when your change touches them:

```bash
LITELLM_BIN=/path/to/litellm uv run pytest tests/test_litellm_integration.py   # through a real LiteLLM proxy
uv run python evals/run.py                                                    # evals against your real gateway
uv run python evals/retrieval.py                                              # code search quality

uv run cmcoder protocol-schema                                     # the Agent Protocol's JSON Schema
uv run cmcoder protocol-schema --typescript > clients/web-panel/src/protocol.ts   # after changing the protocol

cd clients/web-panel && npm ci && npm run typecheck && npm test     # the shared chat page
cd vscode && npm ci && npm run typecheck && uv run --project .. npm test   # the VS Code extension
#   (on Windows: uv run --project .. npm.cmd test, or set CMCODER_TEST_PYTHON to cmcoder's python.exe)
```

The Java and C# parts build with their own tools: `mvn -B verify` in
`clients/jvm-core`, `eclipse` and `netbeans`; `gradle buildPlugin` in
`jetbrains`; `dotnet test visualstudio/test/Cmcoder.Core.Tests`. Each folder's
`README.md` has the details.

## What CI runs

`.github/workflows/ci.yml` on every push:

| Job | Checks |
|---|---|
| test | lint, types, every Python test and the mock evals, on Linux, macOS and Windows |
| integration, code search stores | a real LiteLLM proxy; a real Chroma |
| VS Code extension, IDE core, Visual Studio core and extension, Eclipse, NetBeans, IntelliJ plugin (+ gate) | each front end, its tests and, for IntelliJ, a real IDE |
| security | SAST, secrets, dependency audits, workflow checks |

`release.yml` (Release build) builds every package for Windows, macOS and Linux
and runs each in its real IDE with no Python on the PC ("gates");
`packaging-scripts.yml` runs the one-command packaging scripts. See
[../build-package/release.md](../build-package/release.md).

## Rules that always hold

- **Never disable TLS verification.** Never log or store an API key outside
  `providers/auth.py` (it goes to the OS keychain).
- **Tests never need a real model:** use `cmcoder.testing.mock_server`.
- **Developers' PCs have no Python:** nothing a user installs may need Python,
  pip, uv or Node.js. The standalone build is tested with no Python in reach
  (`packaging/no_python.py`).
- **No server names in the code or docs:** example addresses only
  (`example.com`), and `<your repository URL>`.
- **`docs/project/DESIGN.md` is the source of truth** for architecture
  decisions: update it when a decision changes.

## Adding or changing a feature

1. **Read first:** [source-map.md](source-map.md), the DESIGN section for the
   area, and the phase guide that built it ([README.md](README.md)).
2. **Code and tests together.** A new setting goes in
   `config/settings.py` (with its alias) and gets a test; a protocol change
   regenerates `protocol.ts` and updates each IDE host that needs it.
3. **Docs in the right place** ([../README.md](../README.md)):
   - how to use or set it up → `docs/features/<feature>.md` (and its row in
     [../features/README.md](../features/README.md));
   - a per-IDE step → that IDE's page in `docs/guides/`;
   - building or packaging → `docs/build-package/`;
   - rolling it out (managed settings, branding) → `docs/admin/`;
   - why, and the trade-offs → `docs/project/DESIGN.md`.
4. **Links:** `tests/test_docs_links.py` checks every relative link and
   `#section`; run it after moving or renaming a page.
5. **Security:** a feature that runs something, reads files or talks to the
   network gets a line in the next security review
   ([../project/](../project/README.md)): what a repository could do with it
   untrusted, and what the managed settings can switch off.

## Where the history is

Each phase's plan, status, test notes and security review are in
[../project/phases/](../project/README.md), and each finished phase is a git
tag (`git checkout phase3`) so you can see the code as it was.
