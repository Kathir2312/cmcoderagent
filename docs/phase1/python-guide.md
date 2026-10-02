# cmcoder Phase 1, explained for Python developers

This guide grows as Phase 1 is built: each feature gets its section when it
lands, written in the same style as the
[Phase 0 guide](../phase0/python-guide.md). Read that one first; this guide
assumes you know the Phase 0 code (the agent loop, tools, permissions, the
context budget).

Each section follows the same pattern:

1. **The problem**: what went wrong without the feature, ideally from a real session.
2. **The idea**: the approach in plain words, before any code.
3. **The code**: the files involved, walked through in the order they run.
4. **New Python ideas**: anything not already covered in the Phase 0 guide.
5. **The tests**: how the feature is tested, and how to run just those tests.
6. **Try it**: a small change you can make to see it working.

Status of each section follows [PLAN.md](PLAN.md).

---

## 1. Auto-compaction

*Not started.* Will cover: why a 32K window fills up, measuring tokens,
asking a model to summarise a conversation, keeping tool-call ids paired, and
the `/compact` command.

## 2. Permission prompt never scrolls off screen

*Not started.* Will cover: terminal height, trimming long previews, and
testing terminal output with a pseudo-terminal.

## 3. Detecting the real context window

*Not started.* Will cover: reading numbers out of error messages with regular
expressions, and caching results in a JSON file.

## 4. Steering the model away from Bash for file work

*Not started.* Will cover: how prompts and tool descriptions shape model
behaviour, recognising shell patterns, and writing evals that score tool choice.

## 5. Managed settings

*Not started.* Will cover: per-OS file locations, why the Windows path is
looked up through the Windows API, merging settings layers, and "failing closed".

## 6. Sessions and resume

*Not started.* Will cover: JSON Lines files, saving and loading conversations,
file permissions, and restoring a session cleanly after an interrupt.

## 7. Checkpoints and `/rewind`

*Not started.*

## 8. TodoWrite

*Not started.*

## 9. Small/fast model jobs

*Not started.*

## 10. Tool-call robustness

*Not started.*

## 11. Textual TUI

*Not started.* Will cover: event-driven UIs, Textual widgets and screens, and
running the agent alongside the UI with `asyncio`.

## 12. More evals

*Not started.*
