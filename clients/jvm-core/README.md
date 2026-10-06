# cmcoder IDE core (JVM)

The part of the Eclipse and NetBeans plugins that is the same for both, in
plain Java 17 with no dependencies (so it can't clash with an IDE's own
libraries). Each plugin adds only what needs its IDE's APIs, by implementing
`Ide`.

| Class | Does | Host duties (docs/phase6/PLAN.md) |
|---|---|---|
| `ProgramLocator` | finds the cmcoder program: the user's setting (a full path), else the copy in the plugin, else PATH without the project folder; never a batch file, never Python; restores the executable bit and clears macOS quarantine | H1 |
| `AgentProcess` | starts `cmcoder --protocol stdio` (no shell), reads its JSON lines as UTF-8, sends messages, stops it (shutdown, end of input, then by force, with everything it started) | H2–H4 |
| `Protocol` | the events a host reads and the messages it sends, checked against `cmcoder protocol-schema` by the tests | H6 |
| `EditorContext` | the active file, selection and problems sent with a message, and the chat's label for it | H7 |
| `Host` | everything else between cmcoder, the chat page, the Agent Navigator page and the IDE: relaying, permissions, diffs, IDE tools, rewind, history, links | H5–H18 |
| `Ide` | what a plugin implements: deliver to the pages, editor context, diffs, IDE tools, pickers, open links, log | |
| `Json` | a small JSON reader/writer | |

```
mvn -B verify
```

The tests start the real cmcoder: `CMCODER_TEST_BINARY` (the standalone
program, as developers get it), or else cmcoder from source through
`CMCODER_TEST_PYTHON` (macOS, Linux). The mock model server always comes from
`CMCODER_TEST_PYTHON`. The release workflow runs them with the standalone
program and no Python in reach (`packaging/no_python.py`).
