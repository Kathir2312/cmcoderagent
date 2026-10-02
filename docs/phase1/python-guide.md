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

*Done.* Code: `src/cmcoder/core/compaction.py` (new), `core/agent.py`
(`Agent.compact`, the checks in `Agent.run`), `core/context.py`
(`ContextBudget.distrust`), `cli/repl.py` (`/compact`), `cli/factory.py`
(`build_summarizer`). Tests: `tests/test_compaction.py`, plus a `/compact`
test in `tests/test_repl_pty.py`.

### The problem

The model can only "see" a limited amount of text at once: its **context
window**. Your Qwen3 models have 32,768 tokens (a token is roughly 3–4
characters of code). Every request sends the *whole* conversation: the
system prompt, your messages, every reply, and every tool output. One 2,000-line
file read is about 8,500 tokens, so a few reads fill the window.

Phase 0 coped by replacing the oldest tool outputs with "[Older tool output
removed…]". That keeps the session alive, but the model forgets what it read,
and on a long task it loses track of what it was doing.

### The idea

When the conversation reaches **80%** of the window, cmcoder:

1. keeps the most recent messages (about a quarter of the window) as they are;
2. sends everything older to a model with the instruction "summarise this
   conversation so the agent can continue from your summary";
3. replaces the older messages with that one summary.

Then the next request is much smaller, and the model still knows the user's
requests, the decisions made, the files touched, test results and next steps.

```
before:  [system] [user] [asst] [tool] [asst] [tool] … [asst] [tool]   ≈ 26,000 tokens
after:   [system] [summary]                         [asst] [tool]      ≈  9,000 tokens
                   └─ written by the small model ─┘ └─ kept as is ─┘
```

You can also run it yourself with `/compact`, optionally saying what to keep:
`/compact keep the failing test names`.

### The code

**Where to cut** (`split_index`). The kept part must start at a *step
boundary*, a user or assistant message. It can never start with a tool
result: the server rejects a tool result whose tool call is missing. So the
function lists the boundaries and walks back from the end while the tail
still fits in the "keep" budget:

```python
boundaries = [i for i in range(1, len(messages)) if messages[i].role in ("user", "assistant")]
chosen = boundaries[-1]                 # always keep at least the latest step
for i in reversed(boundaries):          # walk from the newest to the oldest
    if message_tokens(messages[i:], chars_per_token) > keep_tokens:
        break                           # adding this step would be too much
    chosen = i
```

- `range(1, len(messages))` skips index 0, the system prompt, which is never
  summarised.
- `reversed(...)` walks a list backwards without copying it.
- `messages[i:]` is everything from position `i` to the end: the tail we'd keep.

**Turning messages into text** (`render`). The summarising model gets a plain
transcript ("## User …", "## Assistant … (called Read with {…})", "## Result of
Read …"). Long tool outputs are clipped to 1,500 characters with
`truncate_middle`, since the summary only needs the gist.

**Summarising in chunks** (`summarise`). The small model has its own window.
If the transcript is bigger than that, `chunk_transcript` cuts it into pieces
and cmcoder summarises piece by piece: "Here is the summary so far, here is
the next part, rewrite the summary to cover both." If the server still says a
piece is too long, the pieces are halved and it tries again.

**The request survives.** If the cut falls in the middle of a long task, your
request was in the summarised part. cmcoder copies it **word for word** into
the summary message (`REQUEST_MARKER`) rather than trusting the model to
quote it, and carries it over from one summary to the next. (A test caught
the request getting lost on the second compaction before this was added.)

**Which model writes it** (`build_summarizer` in `factory.py`, and
`Agent._summarizers`). The `smallFastModel` from settings (Qwen3 ~7B) if
set, so the big model's capacity is kept for the real work; if that fails,
the main model; if that fails too, a warning and the Phase 0 behaviour
(dropping old tool output). After one failure, auto-compaction is switched off
for the rest of that turn, so a broken model isn't retried before every step.

**In the agent loop** (`Agent.run`). Before each model call:

```python
if can_compact and budget.estimate(self.messages) >= budget.window * self.compact_threshold:
    async for e in self.compact(trigger="auto"):
        yield e
```

`Agent.compact` is an **async generator** like `Agent.run`: it `yield`s a
`Compacted` event (how many messages, tokens before and after, which model),
or a `Warning`. It builds the new message list first and only then assigns
`self.messages = res.messages`, so pressing Ctrl+C during `/compact` leaves
the conversation untouched.

**When the estimate is wrong.** cmcoder estimates tokens from characters. If
the server still answers "maximum context length exceeded", cmcoder now
compacts (instead of only dropping output) and retries. Two related fixes:
- `ContextBudget.distrust()` lowers the characters-per-token estimate *and*
  caps it, so later calibration can't return to a value that already failed.
- The retry is allowed once per model call instead of once per turn, so a
  long turn can recover more than once.

### Settings

```json
{
  "smallFastModel": "corp:qwen3-7b",
  "autoCompact": true,
  "autoCompactThreshold": 0.8
}
```

`autoCompactThreshold` must be between 0.3 and 0.95 (pydantic checks it with
`Field(..., ge=0.3, le=0.95)`: "greater or equal", "less or equal").

### New Python ideas

- **`for … else`**: in `compact()`, the `else:` after a `for` loop runs only
  if the loop finished *without* `break`. Here: "no summariser succeeded".
- **`str.partition(sep)`**: splits a string at the first `sep` into
  `(before, sep, after)`; `sep` comes back empty if it wasn't found. Used to
  pull the word-for-word request out of an earlier summary.
- **`dataclass`es as return values**: `CompactionResult` bundles the new
  messages, the summary, usage and model name, which is clearer than returning
  a 5-item tuple.
- **Catching broad exceptions on purpose**: `except Exception` around each
  summariser means "any failure: try the next model". `asyncio.CancelledError`
  is not an `Exception` subclass, so Ctrl+C still cancels.

### The tests

`tests/test_compaction.py`:

- **Where to cut**: the tail never starts with a tool result, the latest step
  is always kept, and nothing happens when there is nothing to summarise.
- **The acceptance check**: a 60-step session (60 file reads) runs to the end
  against a mock server that rejects any request larger than 32K, exactly as
  vLLM does (`enforce_context=True`). The test checks:
  - compaction happened at least twice, each time making the conversation smaller;
  - every tool call still has its result;
  - your request is still there word for word.
- **Chunks**: a small model with an 8K window has to summarise a big history
  in several calls, and the later calls start with "Summary so far:".
- **Fallbacks**: a broken summariser falls back to the next model; when all
  fail, the run still finishes (old output dropped) with one warning, not one
  per step.
- **Server overflow**: a server that under-reports usage leads to a
  "maximum context length" error, then compaction, then success.
- **`/compact`**: "Nothing to compact yet" on a new conversation; the focus text
  reaches the summariser; only the summary and the latest step remain.
- **Wiring**: `smallFastModel` is used for summaries, with the main model as
  fallback; bad threshold values are rejected.

```bash
uv run pytest tests/test_compaction.py -v
```

### Try it

Set `"autoCompactThreshold": 0.3` in `.cmcoder/settings.json` in a scratch
project, ask cmcoder to read a few large files, and watch for
"✻ Context nearly full: compacted the conversation…". Then run `/compact` by
hand and ask "what have we done so far?" to see what the summary kept.

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

*Done.* Code: `providers/openai_compat.py` (`parse_context_window`,
`probe_context_window`), `providers/profiles.py` (`resolve_profile`),
`cli/factory.py` (`resolve_model_profile`, `load_learned_window`,
`save_learned_window`), `core/agent.py` (the `ContextTooLong` handler),
`cli/doctor.py` (`check_context_window`). Tests: `tests/test_context_window.py`,
plus a test through a real LiteLLM proxy in `tests/test_litellm_integration.py`.

### The problem

Everything in item 1 depends on knowing the window size: "compact at 80% of
*what*?" Your gateway doesn't expose LiteLLM's `/model/info`, so cmcoder
guessed 32,768 tokens for Qwen3. If the real window is bigger, cmcoder
summarises far too early and wastes the model's memory. If it's smaller,
requests fail.

### The idea

The server itself knows its limit, and it says so when you exceed it. vLLM
answers an over-long request with:

```
This model's maximum context length is 40960 tokens. However, you requested 10000010 tokens …
```

So cmcoder can ask on purpose: send the word "hi" with
`max_tokens = 10,000,000`. The server rejects that straight away, without
generating anything, and the error states the limit. That is the **probe**.
It runs once per model, and the answer is saved for 30 days (for a day if the
server didn't say, so it's retried tomorrow, not on every start).

cmcoder also learns **during a session**: if a request is rejected and the
error names a smaller limit than cmcoder assumed, it switches to that limit
on the spot, saves it, and carries on.

Where the window comes from, most trusted last:

| Source | Example | Shown by `doctor` as |
|---|---|---|
| built-in default | 32,768 for Qwen3 | `built-in default` |
| LiteLLM `/model/info` | the gateway's config | `server (/model/info)` |
| what the server enforces | the probe, or an error during a session | `server limit (probe)` / `server limit (from its error)` |
| your settings | `"modelProfiles": [{"match": "qwen3*", "contextWindow": 40960}]` | `settings (modelProfiles)` |

Your explicit setting wins, so you can always correct a wrong value. If your
setting is *larger* than what the server allows, `doctor` warns you.

### The code

**Reading the number** (`parse_context_window`). Different servers word it
differently, so there is a list of **regular expressions**, patterns that
describe text:

```python
_WINDOW_PATTERNS = [
    re.compile(r"maximum context length is (\d+)"),   # vLLM, OpenAI, LiteLLM
    re.compile(r"max_model_len\W{0,4}(\d+)"),           # newer vLLM
    re.compile(r"context (?:length|window|size) (?:is |of )?(?:only )?\(?(\d+) tokens"),  # llama.cpp
    re.compile(r"must have less than (\d+) (?:input )?tokens"),  # TGI
]
```

- `\d+` means "one or more digits"; the parentheses `( )` **capture** them,
  so `m.group(1)` gives the number.
- `(?: … )` groups without capturing; `?` after it means "optional".
- `\W{0,4}` means "up to 4 non-word characters" (like `=` or `: `).
- Commas are removed first, so "40,960" becomes "40960".
- Numbers below 512 are ignored, so an unrelated small number isn't
  mistaken for a window.

`classify_http_error` now stores the number on the error:
`err.context_window = parse_context_window(raw)`.

**The probe** (`probe_context_window`). It sends the request with
`client.stream(...)` and looks at the status code:

- **400**: read the error and return the number;
- **200** (the server accepted it, so it doesn't check): leave the `with`
  block at once, which closes the connection so the server stops generating,
  and return `None`.

It's wrapped in `asyncio.wait_for(..., timeout)`, so a slow server can't hold
up start-up for more than 8 seconds.

**The cache** (`load_learned_window` / `save_learned_window` in
`factory.py`). This is a JSON file, `~/.cmcoder/cache/context_windows.json`,
keyed by gateway URL and then model name:

```json
{"https://gateway/v1": {"Qwen3.6-27B": {"tokens": 40960, "source": "server limit (probe)", "at": 1790000000.0}}}
```

`at` is a Unix timestamp (`time.time()`, seconds since 1970). Comparing it
with the current time tells whether the entry has expired.

**Choosing the value** (`resolve_model_profile`). If nothing is known yet (no
setting, no `/model/info`, no cache entry), it probes; then it calls
`resolve_profile(..., learned_window=(tokens, source))`, which applies the
order in the table above and records `context_window_source` on the profile.

**Learning mid-session** (`Agent.run`). When the server rejects a request:

```python
if e.context_window and e.context_window < self.profile.context_window:
    self.profile = self.profile.model_copy(update={"context_window": e.context_window, ...})
    if self.on_context_window:
        self.on_context_window(self.model, e.context_window)   # saves it to the cache
    yield ev.Warning(message=f"The server says {self.model}'s context window is ...")
```

`model_copy(update=...)` is pydantic's way to get a changed copy of a model
object. The original isn't modified, which is safer than editing it in place.
`on_context_window` is a **callback**, a function passed in from outside. The
agent doesn't know about cache files; `factory.py` hands it a small `lambda`
that saves to the cache. That keeps `core/` free of file-system details.

**`doctor`** (`check_context_window`) always probes fresh and reports the
value, its source, and warnings: a window of 32K or less ("small for an
agent"), or a setting larger than what the server allows.

### New Python ideas

- **Regular expressions** (`re` module): `re.compile(pattern)` once, then
  `.search(text)` returns a match or `None`; `match.group(1)` is the first
  captured part.
- **`model_copy(update={...})`** (pydantic): a modified copy of an object.
- **Callbacks / `lambda`**: `lambda m, n: save(..., m, n, ...)` is a one-line
  unnamed function, handy for small callbacks.
- **`asyncio.wait_for(coro, timeout)`**: gives up on an `await` after
  `timeout` seconds, raising `TimeoutError`.

### The tests

`tests/test_context_window.py`:

- the parser on real error texts from vLLM, LiteLLM, llama.cpp and TGI, plus
  texts with no number or a misleading small number;
- the probe returns the mock server's limit, and `None` (quickly) when the
  server accepts the request;
- your gateway's case: no `/model/info` → probe once → 40,960 used and cached
  → the next start doesn't probe again;
- no probe when the window is known (settings, `/model/info`, or a recent "didn't say");
- **mid-session learning**: cmcoder assumes 32K, the server allows only 20K;
  the first rejection teaches it the real limit, the callback saves it, and
  the session finishes;
- `doctor` shows the value and where it came from, and flags a setting
  larger than the server allows.

`tests/test_litellm_integration.py::test_context_window_probe_through_gateway`
runs the probe through a real LiteLLM proxy. The backend allows 40,960 while
LiteLLM's config claims 32,768, and the probe correctly reports 40,960.

```bash
uv run pytest tests/test_context_window.py -v
```

### Try it

Run `cmcoder doctor` against your gateway and look for the
"context window" line: it now says what the server reported. To see the
cache, open `~/.cmcoder/cache/context_windows.json` (on Windows:
`%USERPROFILE%\.cmcoder\cache\context_windows.json`).

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
