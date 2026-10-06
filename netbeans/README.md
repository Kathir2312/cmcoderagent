# cmcoder for Apache NetBeans

The chat, the Agent Navigator and proposed changes in NetBeans' diff viewer,
for **NetBeans 28 to 31** (the latest four releases), on JDK 17 or newer.
cmcoder itself and the browser for the chat (OpenJFX's WebView: NetBeans has
none of its own) are inside the plugin file, so developers install one file and
need nothing else: no Python (docs/DESIGN.md D16).

User guide: [docs/guides/netbeans.md](../docs/guides/netbeans.md).

## For maintainers: build and test

| Path | What |
|---|---|
| `src/main/java/cmcoder/netbeans` | the module (it also compiles `clients/jvm-core`, as Eclipse does) |
| `src/gate/java` | the release gate's self-test (`Gate.java`), in the test build only |
| `gate/run-gate.sh` | runs the gate in a real NetBeans (Linux, macOS, Git Bash on Windows) |

```
(cd clients/web-panel && npm ci && npm run build)
uv run --with pyinstaller python packaging/build.py      # the standalone cmcoder
mkdir -p netbeans/bin && cp -RL dist/cmcoder netbeans/bin/cmcoder

cd netbeans
mvn -B package -Dplatform=linux        # or win, mac-aarch64 → target/cmcoder-netbeans-<target>.nbm
mvn -B package -Dplatform=linux -Pgate # the test build (never shipped)

# the gate: NetBeans unpacked from archive.apache.org; Xvfb on Linux
xvfb-run -a uv run python packaging/no_python.py run -- bash netbeans/gate/run-gate.sh \
  --nbm netbeans/target/cmcoder-netbeans-linux-x64.nbm --netbeans /path/to/netbeans \
  --python "$(uv run python -c 'import sys; print(sys.executable)')"
```

One `.nbm` per platform: the module, OpenJFX 21 for that platform (it runs on
JDK 17 to 25; JDK 24 and 25 still carry the `jdk.jsobject` module it needs) and
cmcoder. The module requires its OS (`org.openide.modules.os.*`), so the wrong
file can't be enabled.

The gate puts the `.nbm`'s files into a fresh NetBeans user folder, where
NetBeans' installer puts a plugin (`netbeans --modules --install` first
refreshes NetBeans' update catalog, which a closed network refuses), starts the
mock model server and NetBeans, and the self-test runs a conversation through
the real chat page: the bundled cmcoder starts, the editor context, Copy
Diagnostics without the key, `getDiagnostics`, a change accepted and one
rejected in the diff tab, the navigator, a navigation away from the page
refused, Ask About Selection, and closing the chat stopping cmcoder. CI runs it
on NetBeans 28 (JDK 21) and 31 (JDK 25) on Linux; the release build on every
platform with that platform's cmcoder.

Notes:

- Messages from the page come through `alert()` with a secret made for each
  page load; no Java object is exposed to the page (a JavaFX `JSObject` member
  would let a script call any of its public methods).
- JavaFX can't refuse a navigation; one away from the page is cancelled as it
  starts, and the chat page is loaded again if another one ever loads.
- `getDiagnostics` and the editor context's problems come from the error
  providers NetBeans registers per file type (`org.netbeans.spi.lsp.ErrorProvider`,
  the ones its language server uses): Java and other languages that have one.
  With no file named, it covers the files open in editors.
- Not carried over: the "extra arguments" setting (see the Eclipse notes).
