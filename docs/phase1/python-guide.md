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

*Done.* Code: `src/cmcoder/cli/repl.py` (`clip_preview`, `short_rule`,
`Repl.ask`). Tests: `tests/test_repl_prompt.py`, `tests/test_repl_pty.py`.

### The problem

Qwen sometimes writes a whole file through Bash:

```bash
cat > big.txt << 'EOF'
row 1
...
row 500
EOF
```

cmcoder asks before running it and shows the command in a box. The box
printed all 500 lines, and the "1 / 2 / 3" choices below it scrolled off the
top of the window. You had to scroll up to read them.

Writing the test exposed a **second** cause. Option 2 says "don't ask again
for `Bash(...)`", and for a multi-line command that rule *is* the whole
command, so the 500 lines were printed a second time.

### The idea

1. Work out how many lines the terminal has, and how many the prompt needs
   for everything except the preview (border, title, the three options, the
   input line, the toolbar).
2. Show only as many preview lines as fit: the first ones, a marker
   "… N more lines (v to view all) …", and the last few.
3. Print the options **after** the preview, and repeat them in the input line
   itself (`1 yes · 2 always · 3 no · v view all:`), so they're always right
   where you type.
4. Show the rule in option 2 as a single, shortened line.
5. If the answer isn't valid (say `11`), say so and show the options again.

### The code

`clip_preview(text, max_lines)` is a plain function, so it's easy to test:

```python
lines = text.splitlines()              # a list of lines
if len(lines) <= max_lines:
    return "\n".join(lines), 0          # short enough: nothing hidden
tail = max(1, max_lines // 3)          # keep about a third at the end
head = max_lines - tail - 1            # the rest at the start, minus the marker line
hidden = len(lines) - head - tail
shown = [*lines[:head], f"… {hidden} more lines (v to view all) …", *lines[-tail:]]
```

- `lines[:head]` is a **slice**: the first `head` items. `lines[-tail:]` is
  the last `tail` items (negative numbers count from the end).
- `[*a, x, *b]` builds one list from list `a`, then the item `x`, then list
  `b`. The `*` "unpacks" a list into the new one.
- The function returns a **tuple** `(text, hidden)`. The caller unpacks it
  with `shown, hidden = clip_preview(...)`.
- Very long single lines (minified code, for example) are also cut at 400
  characters, because one such line wraps into many screen rows.

`Repl._preview_lines()` asks rich how tall the terminal is
(`self.console.size.height`) and subtracts `PROMPT_CHROME_LINES` (12), the
lines the prompt needs besides the preview. The result is capped at 30.

`Repl.ask()` now:

1. gets the preview text and its colouring (`bash`, `diff` or plain text)
   from `_preview_source()`, one place for all tools;
2. clips it and prints it in a panel;
3. prints the options with `_print_options()`;
4. loops on the input: `1` / `2` / `3` as before, `v` prints everything with
   line numbers and then the options again, and anything else prints
   "Please answer …" and the options again (`continue` jumps back to the start
   of the `while` loop).

`short_rule()` keeps only the first line of a rule and at most 60 characters,
so the heredoc rule shows as `Bash(cat > big.txt << 'EOF' …)`.

### New Python ideas

- **Slices with negative numbers**: `items[-3:]` means "the last three".
- **Unpacking with `*`** inside a list: `[*a, *b]` joins lists.
- **`@staticmethod`**: a function that lives in a class but doesn't use
  `self`. `_preview_source` only needs the request, not the REPL object.
- **`Text.assemble(...)`** (rich): builds styled text from pieces. Used
  instead of rich's `[cyan]…[/cyan]` markup so that a `[` inside a command
  can't be mistaken for markup.

### The tests

- `tests/test_repl_prompt.py` tests `clip_preview` and `short_rule` directly:
  short text unchanged, long text keeps head and tail, a tiny terminal still
  shows three lines, very long lines are cut. These run on every OS.
- `tests/test_repl_pty.py::test_long_preview_keeps_options_on_screen` starts
  the real `cmcoder` in a **pseudo-terminal** (a fake terminal that a program
  can't tell from a real one) sized 80×24. The mock model asks to run the
  500-line heredoc. The test checks:
  - everything from the panel title to the input line fits in 24 lines;
  - `row 1` and `row 500` are visible but `row 250` isn't;
  - typing `11` re-shows the options, and `v` shows `row 250`;
  - answering `3` stops without creating the file.

  Pseudo-terminals don't exist on Windows, so this test is skipped there. The
  unit tests above still run on Windows.

Run just these tests:

```bash
uv run pytest tests/test_repl_prompt.py tests/test_repl_pty.py -v
```

### Try it

Change `MAX_PREVIEW_LINES` in `repl.py` from 30 to 10, run `cmcoder`, and
ask it to create a 100-line file with a shell command. Count the preview
lines, then press `v`.

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
