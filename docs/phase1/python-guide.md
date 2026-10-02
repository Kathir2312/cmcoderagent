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

*Done.* Code: `src/cmcoder/core/steer.py` (new), the Bash check in
`Agent._run_call` (`core/agent.py`), `core/prompt.py`, the tool descriptions
in `tools/bash.py` and `tools/files.py`, and `evals/run.py`. Tests:
`tests/test_steer.py`, plus two new eval tasks (`create-file`, `inspect-files`).

### The problem

In your trials Qwen3.6 did file work through the shell. It wrote files with
`cat > file << 'EOF' … EOF`, read them with `cat` or `python -c "open(...)"`,
and searched with `grep -r` and `find`. That causes three problems:

1. **Every call needs your approval.** Bash commands ask; Read, Grep and Glob
   don't.
2. **cmcoder's safety checks are skipped.** Write and Edit refuse to change a
   file the model hasn't read, or one that changed on disk since. A heredoc
   bypasses all that.
3. **The permission prompt gets flooded** with the whole file, which was the
   item 2 bug.

### The idea

Three layers, from gentle to firm:

1. **Tell it.** The system prompt now has an explicit rule: "Write instead of
   `cat > file << EOF`… Bash is for running programs." The Bash tool
   description says "Do NOT use it for file work" and lists the alternatives.
2. **Catch it.** When a Bash call is *plainly* file work, cmcoder doesn't run
   it and doesn't ask you. It answers the model with the exact tool call to
   use instead, for example:
   `Not run: to create or overwrite notes.md, use the Write tool (file_path="notes.md", content=the full text).`
   Models are good at following a concrete correction like this.
3. **Measure it.** The evals now count which tools the model used, and how
   often it tried file work through Bash.

"Plainly" matters: only one simple command is redirected. Pipelines
(`cat a | grep x`), chains (`make && cat out`), scripts fed to Python
(`python3 - << EOF`) and anything unusual run as before. If the model really
needs the shell, it can **send the same command again** and it goes through
the normal permission flow, so it's never stuck.

| Bash command | Redirected to |
|---|---|
| `cat > f << EOF`, `cat << EOF > f`, `tee f << EOF`, `echo … > f` | Write |
| `cat >> f << EOF`, `echo … >> f`, `sed -i …` | Edit |
| `cat f`, `head -n 50 f`, `tail -n 30 f`, `sed -n '10,40p' f`, `python -c "print(open('f').read())"` | Read (with offset/limit) |
| `grep -rn pattern dir`, `grep pattern file`, `rg pattern` | Grep |
| `find dir -name '*.py'` | Glob |

### The code

`file_work_redirect(command)` returns a message, or `None` to run the
command. It checks in this order:

1. **Heredoc writes**, by looking at the first line with three regular
   expressions, one per way of writing it. The building blocks are shared:

   ```python
   _DELIM = r"""<<-?\s*['"]?\w+['"]?"""           # << EOF, <<-'EOF', << "END"
   _TARGET = r"""(['"]?)([^\s'"<>|;&]+)\2"""     # a file name, maybe quoted
   ```

   `\2` is a **backreference**: "the same quote character that group 2
   matched", so `'file'` and `"file"` both work but `'file"` doesn't.
2. **`python -c` with `open(`**: a write if it opens with `"w"`/`"a"` or calls
   `.write(`, otherwise a read.
3. **Anything with a pipe, `;`, `&`, a backtick or `$(`** → run it (too
   complex to be sure).
4. **`echo`/`printf` redirected into a file.** The `(?<![0-9&])` in that
   pattern is a **negative lookbehind**: it skips `2> err.txt` (error output)
   and `&> f`. Writes to `/dev/null` are allowed.
5. **The rest is split into words** with `shlex.split` (shell-style, so
   quotes are handled). Then `cat`, `head`, `tail`, `sed`, `grep`/`rg` and
   `find` are checked one by one. Unusual options mean "run it": `tail -f`
   (follow), `grep -A 3` (its value `3` would be mistaken for the pattern),
   `find … -delete`.

In `Agent._run_call`, before the permission check:

```python
if tool.name == "Bash" and self.profile.steer_bash_file_work:
    hint = file_work_redirect(command)
    if hint and command != self._redirected:     # same command twice: let it through
        self._redirected = command
        yield finish(ToolResult(hint + " If the shell is really needed …", is_error=True))
        return
    self._redirected = None
```

Nothing is run and no permission is asked, so this can't weaken security.
At worst the model is told to use a tool that has *more* checks.

To turn it off for a model, use `"modelProfiles": [{"match": "qwen3*", "steerBashFileWork": false}]`.

### Scoring tool choice in the evals

`evals/run.py` now reads cmcoder's event stream (`--output-format stream-json`)
and counts every `tool_use` event. It flags the Bash ones that
`file_work_redirect` would catch. Each task line shows
`tools=[Grep:1 Read:1] bash-file-work=0`, and the summary shows the share of
file work done with the file tools:

```
Tool choice: 16 file-tool calls, 2 attempts at file work through Bash (88% of file work done with the file tools)
```

Two new tasks focus on this: `create-file` ("create settings.ini with …") and
`inspect-files` ("which file defines load_orders …"). Their mock scripts
deliberately start with the bad habit (a heredoc, a `grep -rn`), so CI checks
that the redirect happens and the model recovers. With your real model, the
number to watch is that percentage.

### New Python ideas

- **Backreferences** (`\2`) and **lookbehind** (`(?<!…)`) in regular expressions.
- **`shlex.split`**: splits a command line the way a shell does, so
  `grep -rn 'def main' src` gives `['grep', '-rn', 'def main', 'src']`.
- **Tuples in a list as a lookup table**: `_HEREDOC_WRITES` holds
  `(pattern, operator group, path group)` triples, and the loop unpacks them
  with `for pattern, op_group, path_group in _HEREDOC_WRITES:`.

### The tests

`tests/test_steer.py`:
- 20 commands that must be redirected, each checked for the right tool and
  arguments (file name, `offset`/`limit`, pattern, path);
- 16 that must still run: pipelines, chains, `python3 - << EOF`, `tail -f`,
  `grep -A 3`, `find -delete`, writes to `/dev/null`, `2>`;
- your trial case end to end: the heredoc is answered *without a permission
  prompt*, the model then uses Write, and the file is created;
- sending the same command again goes to the normal permission flow;
- `steerBashFileWork: false` restores the old behaviour;
- the system prompt (both tiers) and the Bash description carry the rule.

```bash
uv run pytest tests/test_steer.py -v
uv run python evals/run.py --mock
```

### Try it

Against your gateway: `uv run python evals/run.py` and look at the "Tool
choice" line. Then in a session, ask "create a file hello.txt containing hi"
and watch which tool Qwen picks.

## 5. Managed settings

*Done.* Code: `config/settings.py` (`managed_settings_path`,
`read_managed_settings`, the end of `load_settings`), `compat.py`
(`program_files_dir`), `core/permissions.py` (the `mode` property,
`available_modes`, `allow_rules_locked`), `cli/factory.py` (`build_agent`),
`cli/repl.py` (`/mode`, Shift+Tab, the banner), `cli/doctor.py`
(`check_managed`). Tests: `tests/test_managed.py`. Example file:
[`docs/managed-settings.example.json`](../managed-settings.example.json).

### The problem

Your security team wants rules that **developers can't loosen**. Every
settings file so far belongs to the developer: `~/.cmcoder/settings.json`, the
project's `.cmcoder/settings.json`, `settings.local.json`. Any of them can turn
on `bypassPermissions`, add `"allow": ["Bash"]`, or point cmcoder at a
different server.

### The idea

One more settings file, **owned by administrators**, in a place ordinary
users can't write:

| OS | Managed settings file |
|---|---|
| Windows | `C:\Program Files\cmcoder\managed-settings.json` |
| macOS | `/Library/Application Support/cmcoder/managed-settings.json` |
| Linux | `/etc/cmcoder/managed-settings.json` |

It's applied **last**, after every other layer, so it always wins. What it can
enforce:

| Key | Effect |
|---|---|
| `permissions.disableBypassPermissionsMode: "disable"` | `bypassPermissions` can't be chosen: not by `--permission-mode`, `defaultMode`, `/mode` or Shift+Tab |
| `permissions.highRiskCommands: "deny"` | high-risk commands (`rm -rf`, force push, `curl \| sh` …) never run |
| `permissions.deny: [...]` | deny rules that always apply (they're added to everyone else's) |
| `permissions.allowManagedPermissionRulesOnly: true` | only the managed `allow` rules count: other files' allow rules, `--allowedTools` and "always allow" answers are ignored |
| `lockProviders: true` | only the managed `providers` (gateway URL, CA) can be used; other providers and `CMCODER_BASE_URL` are ignored |
| `env: {...}` | environment variables that override the user's |

Three security rules shape the code:

1. **It can't be moved.** No environment variable or flag changes where
   cmcoder looks.
2. **It fails closed.** If the file exists but is broken or unreadable,
   cmcoder refuses to start instead of quietly running without the rules.
3. **Only the managed file can use the managed-only keys.** If a project's
   `.cmcoder/settings.json` says `lockProviders: true`, that's ignored.
   Otherwise a downloaded repository could lock you to *its* server.

### The code

**Finding the file** (`managed_settings_path`). `sys.platform` tells the OS:
`"win32"`, `"darwin"` (macOS) or `"linux"`. On Windows the folder isn't read
from `%ProgramFiles%`, because any user can change environment variables for
their own programs. Instead `compat.program_files_dir()` asks Windows itself,
through the **Known Folders** API, using **ctypes** (Python's way to call C
functions in system DLLs):

```python
out = ctypes.c_wchar_p()                                      # will receive a string pointer
hr = ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out))
if hr == 0:                                                   # 0 means success in Windows APIs
    path = Path(out.value)
    ctypes.windll.ole32.CoTaskMemFree(out)                    # Windows allocated it; we free it
```

The `guid` is a 16-byte ID that names the folder (FOLDERID_ProgramFiles). Why
not `C:\ProgramData`? Ordinary users can create folders there, so a
developer could create `C:\ProgramData\cmcoder\` before IT does and own it.

**Reading it** (`read_managed_settings`):

```python
try:
    text = path.read_text(encoding="utf-8")
except FileNotFoundError:
    return {}                       # no file: nothing is managed
except OSError as e:
    raise SettingsError(...)        # exists but can't be read: refuse to start
```

`FileNotFoundError` is a *subclass* of `OSError`, so it must be caught
**first**. `except` clauses are tried top to bottom.

**Merging** (end of `load_settings`):
1. `_strip_managed_only()` removes the managed-only keys from the user,
   project and local files as they're read.
2. Under `lockProviders`, the environment's gateway URL is dropped (the
   model name from `CMCODER_MODEL` still counts).
3. The managed file is merged last with `deep_merge`, so its values win and
   its `deny` rules are added.
4. Under `allowManagedPermissionRulesOnly`, `allow` is reset to the managed
   list; under `lockProviders`, `providers` is replaced by a copy of the
   managed ones.

**Enforcing the mode** (`PermissionPolicy.mode` is now a **property**). A
property looks like a plain attribute (`policy.mode = "plan"`) but runs code
on every assignment:

```python
@property
def mode(self) -> str:
    return self._mode

@mode.setter
def mode(self, value: str) -> None:
    if value == "bypassPermissions" and self.bypass_disabled:
        raise ModeNotAllowed(BYPASS_DISABLED_MESSAGE)
    self._mode = value
```

So *every* way of changing the mode goes through one check:
- the `--permission-mode` flag;
- `defaultMode` in a settings file;
- `/mode bypassPermissions`;
- Shift+Tab, which cycles through `available_modes()` and skips the
  disabled one.

`build_agent` creates the policy **before** contacting the gateway, so a
forbidden mode fails immediately with a clear message.

**"Always allow"** isn't offered while `allow_rules_locked` is set
(`can_remember=False`), and `add_allow()` ignores rules.

**`doctor`** (`check_managed`) shows the file and what it enforces. It warns
if the current user can write the file or its folder (`os.access(path,
os.W_OK)`), because then it protects nothing.

### What it does and doesn't protect against

Managed settings stop **developers and repositories from loosening the
rules**. They don't protect a machine that's already compromised: an attacker
with administrator rights can edit the file. That needs the sandbox/VM and
gateway-side controls from the security discussion.

### New Python ideas

- **`@property` with a setter**: attribute syntax, with a check behind it.
- **Exception order**: catch the specific subclass (`FileNotFoundError`)
  before the general one (`OSError`).
- **ctypes**: calling Windows API functions from Python, including memory
  the OS allocates and you must free.
- **`sys.platform`** for OS-specific code paths.
- **Monkeypatching in tests**: `monkeypatch.setattr(module, "name", value)`
  temporarily replaces a function. The tests point `managed_settings_path` at
  a temporary file, and `conftest.py` does it for every test, so a real
  `/etc/cmcoder` on your machine can't affect the test run.

### The tests

`tests/test_managed.py`:

- **The file:** none present → nothing managed. Broken JSON, a non-object, or
  an unreadable path → cmcoder refuses to start. `CMCODER_CONFIG_DIR`,
  `%ProgramFiles%` and `%PROGRAMDATA%` can't move it, and the Windows path
  comes from Windows.
- **Deny and high-risk:** managed deny rules and `highRiskCommands: "deny"`
  win over user and project files.
- **Managed-only keys:** they're ignored when a project file sets them.
- **Allow rules:** with managed-only rules, user, project and local allow
  rules and `--allowedTools` are dropped, `--disallowedTools` still adds, and
  "always allow" is neither offered nor saved.
- **Bypass mode:** refused from the flag, user settings or project settings,
  and can't be switched on later (`/mode`, Shift+Tab).
- **Providers:** `lockProviders` drops other providers and `CMCODER_BASE_URL`,
  but keeps the CA path and still lets `CMCODER_MODEL` pick the model. Locking
  without providers is an error.
- **Environment:** the managed `env` wins.
- **doctor:** shows what's enforced and warns when the file is writable.

```bash
uv run pytest tests/test_managed.py -v
```

### Try it

On Linux or macOS (as administrator), copy `docs/managed-settings.example.json`
to the managed path, put in your gateway URL, then run `cmcoder doctor` and
`cmcoder --permission-mode bypassPermissions`. On Windows, use an
administrator prompt and `C:\Program Files\cmcoder\`.

## 6. Sessions and resume

*Done.* Code: `src/cmcoder/core/sessions.py` (new), `Agent.save_session` /
`Agent.resume` / `Agent.clear` (`core/agent.py`), `resume_session`
(`cli/factory.py`), `--continue` / `--resume` (`cli/main.py`), `/resume`
(`cli/repl.py`). Tests: `tests/test_sessions.py`, plus a `/resume` test in
`tests/test_repl_pty.py`.

### The problem

Close the terminal and the conversation was gone. On a long task, or after
a crash, you had to explain everything again.

### The idea

Save every conversation to disk as it happens, one file per conversation:

```
~/.cmcoder/projects/<project-name>-<hash>/<session-id>.jsonl
```

How you get it back:
- `cmcoder --continue` (or `-c`) carries on with the latest conversation in
  this project.
- `cmcoder --resume 3f2a` (or `-r`) picks one by id; the first few characters
  of the id are enough.
- `/resume` inside a session lists the project's conversations to choose
  from.
- `/clear` starts a new conversation, and the old one stays resumable.
- `-p` runs are saved too, so `cmcoder -p --continue "and now…"` works in
  scripts.

Files untouched for `cleanupPeriodDays` (default 30) are deleted at start-up.
`"persistSessions": false` turns saving off; an organisation can enforce
that in managed settings.

### The code

**The file format: JSON Lines** (`.jsonl`). One JSON object per line. That
suits a growing log: adding a line never rewrites the file, and if a crash
cuts the last line in half, only that line is lost. `load()` skips any line
that doesn't parse. The records are:

```json
{"type": "meta", "session_id": "…", "cwd": "…", "model": "qwen3-27b", "created": 1790000000.0}
{"type": "message", "message": {"role": "user", "content": "fix the test", "turn": 1}}
{"type": "message", "message": {"role": "assistant", "tool_calls": [ … ]}}
{"type": "reset", "messages": [ … ]}
```

A `reset` record holds the whole conversation. It's written when the history
was replaced rather than extended (compaction, `/clear`, a failed request
being rolled back). `load()` simply replays the lines in order.

**What gets written** (`SessionLog.save`). The agent calls `save_session()`
after every step and in `finally:` when a turn ends, so a crash loses at most
one step. To know what's new, the log remembers the `id()` of every message
it has written. (`id()` is the object's identity: the same object always has
the same id.)

```python
if ids[:n] == self._saved:          # the old messages are still there, in order
    write the new ones as "message" records
else:                               # the history was replaced
    write one "reset" record
```

**What's not written.** The system prompt isn't saved, because it's rebuilt
on resume so memory files and settings are current. No API keys or settings
are written either. Files are created **owner-only**: `os.open(path,
os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)`, in a folder with mode
`0o700`. `0o600` is octal for "the owner can read and write; nobody else can
do anything". On Windows, your user folder's permissions apply.

**Repairing a cut-off session** (`repair`). If cmcoder was killed while a
tool was running, the file ends with an assistant message that called a tool
but has no tool result. The server would reject that ("tool call without a
result"), so `repair()` adds an "Interrupted" result for every unanswered
call. Ctrl+C was already handled this way during a session
(`_repair_after_interrupt`), and that result is now saved too.

**Which project a session belongs to** (`project_key`). It's a readable name
plus a hash of the project path. `os.path.normcase` lower-cases the path on
Windows, so `C:\Repo` and `c:\repo` count as the same project there.
`hashlib.sha256(...).hexdigest()[:16]` turns any path into 16 safe
characters for a folder name.

**Turn numbers.** Each user message now carries `turn` (1, 2, 3, …). It isn't
sent to the model. It's what item 7's `/rewind` uses to find "the state
before message 3".

### New Python ideas

- **JSON Lines**, and appending with `os.open(..., O_APPEND)`.
- **File modes in octal** (`0o600`, `0o700`) and `stat.S_IMODE` to read them back.
- **`dataclasses.asdict`** turns a dataclass (and nested ones like `ToolCall`)
  into plain dicts for JSON.
- **`id(obj)`**: an object's identity, used to tell "same messages plus new
  ones" from "replaced".
- **`try / finally`** in an async generator: the `finally` block runs whether
  the turn ends normally, with an error, or by Ctrl+C.

### The tests

`tests/test_sessions.py`:
- a turn is saved (owner-only file and folder; no system prompt in the file),
  resumed in a fresh agent, continued, and appended to the same file;
- a crash mid-tool (half-written last line, tool call without result) loads
  as a valid transcript;
- Ctrl+C during `sleep 30` saves a valid transcript;
- compaction writes a `reset` record, and `/clear` starts a new session while
  the old one stays findable;
- Windows paths: `C:\Users\Dev\Repo` and `c:\users\dev\repo\` map to one
  project (the test uses Python's Windows path rules, `ntpath`, so it runs on
  any OS);
- old sessions and their checkpoints are cleaned up;
- the real CLI: `-p`, then `-p --continue` (the second request contains the
  first conversation), and a clear error for an unknown `--resume` id;
- `persistSessions: false` writes nothing.

`tests/test_repl_pty.py::test_resume_command` drives a real terminal:
- chat, then `/clear`;
- `/resume` lists the conversation, choosing `1` resumes it, and the model
  receives the earlier messages.

### Try it

Start `cmcoder`, ask something, quit with `/exit`, then run `cmcoder -c` and
ask "what did I just ask you?". The files are in `~/.cmcoder/projects/` (on
Windows `%USERPROFILE%\.cmcoder\projects\`); open one to see the JSON Lines.

## 7. Checkpoints and `/rewind`

*Done.* Code: `src/cmcoder/core/checkpoints.py` (new), `Agent.rewind` /
`Agent.rewind_points` and the capture in `Agent._run_call` (`core/agent.py`),
`Repl._rewind` (`cli/repl.py`). Tests: `tests/test_checkpoints.py`, plus a
`/rewind` test in `tests/test_repl_pty.py`.

### The problem

The agent edited five files and went the wrong way. Undoing that by hand (or
with git, if you hadn't committed in between) is tedious, and the
conversation still contains the wrong turn, so the model keeps building on
it.

### The idea

Before Write or Edit changes a file for the **first time in a turn**, cmcoder
saves what the file looks like (or notes that it doesn't exist yet). That
saved copy is the **checkpoint**. `/rewind` then lets you pick one of your
earlier messages and choose:

```
  1 code and conversation · 2 conversation only · 3 code only
```

- **code**: every file changed since that message goes back to how it was
  just before it. Files the agent created since then are deleted.
- **conversation**: the history is cut back to just before that message, and
  your message is put back in the input line so you can edit and resend it.

Two limits, both shown on screen:
- **Changes made by Bash commands aren't tracked**: only Write and Edit go
  through checkpoints. (Item 4 makes Bash file writes rare.)
- **Files outside the project** are only restored if you say yes.

### The code

**Storage** (`Checkpoints`). Next to the session file:

```
<session-id>.checkpoints/index.json         which file was captured in which turn
<session-id>.checkpoints/blobs/<sha256>     the contents
```

Contents are stored by their SHA-256 hash (**content-addressed**). Two
identical versions are stored once, and the file name proves the content is
intact. The index is written to a temporary file and then renamed with
`os.replace`, which is **atomic**: a crash leaves either the old index or the
new one, never half of one. Without a session file (`persistSessions: false`)
everything is kept in memory.

**Capturing** (`capture`). Called from `Agent._run_call` right before Write or
Edit runs, after the permission check:

```python
if any(e.turn == turn and e.path == key for e in self.entries):
    return                                   # already captured this turn: keep the oldest
```

Only the first capture per file per turn matters: that's "the file before
this turn".

**What to restore** (`changes_since(turn)`). For each file changed in that
turn or later, take its **earliest** capture from that turn on. That's how it
looked before the chosen message, even if later turns changed it again.

**Restoring** (`restore`). `blob is None` means the file didn't exist, so
delete it; otherwise write the saved bytes back. Afterwards the undone turns'
entries are dropped, and new edits are captured fresh.

**Nothing is lost** (`Agent._keep_before_rewind`). Before the conversation is
cut, the whole conversation as it was is saved as its own session, titled
"Before rewind: …", so `/resume` or `cmcoder -c` can get it back. This was
added after the hands-on test on Windows: rewinding to the very first message
left an empty conversation, and `-c` then had nothing to continue.

**The conversation side** (`Agent.rewind`). It finds the user message with
that `turn` number (item 6 added it), cuts `messages` before it, sets
`self.turn` back and saves, which writes a `reset` record to the session
file. If compaction already summarised that message away, only the code can
be rewound.

### New Python ideas

- **Hashing** with `hashlib.sha256(data).hexdigest()` for content-addressed storage.
- **Atomic replace**: write `x.tmp`, then `os.replace(x.tmp, x)`.
- **`Path.is_relative_to(root)`**: is this file inside the project?
- **`read_bytes` / `write_bytes`**: restoring exact bytes, so line endings
  and encodings are untouched.
- **Pre-filled input**: `prompt_async("> ", default=text)` puts the rewound
  message back for editing.

### The tests

`tests/test_checkpoints.py`:
- capture and restore, on disk and in memory:
  - the first capture in a turn wins;
  - a created file is deleted;
  - restoring to turn 1 after turn 2 gives the turn-1 state;
  - the index survives a restart;
- files over the size limit are skipped, never overwritten;
- the agent across two turns (edit, then edit again and create):
  - rewinding to message 2 restores `x = 2` and deletes `notes.md`;
  - it hands back the message text and rewinds the session file;
  - rewinding code only to message 1 gives `x = 1`;
- conversation-only rewind leaves files alone;
- a file outside the project is left alone unless you agree;
- checkpoints survive `--resume`.

`tests/test_repl_pty.py::test_rewind_command` drives the real terminal: edit,
`/rewind`, choose 1 and 1, the file is restored, and "set x to 2" is back in
the input line.

### Try it

Ask cmcoder to make a change in a scratch project, then `/rewind`, pick that
message, and choose `3` (code only). Run `git status`: the change is gone.

## 8. TodoWrite

*Done.* Code: `src/cmcoder/tools/todo.py` (new), `last_todos` and the
compaction/resume hooks in `core/agent.py`, `core/compaction.py`,
`Repl._print_todos` and `/todos` (`cli/repl.py`), the prompt line in
`core/prompt.py`. Tests: `tests/test_todo.py`, plus a checklist test in
`tests/test_repl_pty.py`.

### The problem

On a task with many steps (say "add a setting, use it in three places,
update the docs, run the tests"), a model easily forgets a step, especially
after compaction has summarised the start of the conversation. You also
can't see its plan.

### The idea

Give the model a tool whose only job is to keep a **visible task list**. It
sends the complete list each time it changes:

```json
{"todos": [
  {"content": "Read the code", "status": "completed"},
  {"content": "Fix the bug", "status": "in_progress", "activeForm": "Fixing the bug"},
  {"content": "Run the tests", "status": "pending"}
]}
```

You see it as a checklist:

```
● Todo list
  ☑ Read the code
  ► Fix the bug
  ☐ Run the tests
```

`/todos` shows it again at any time. It doesn't touch any files, so it never
needs your permission. It's kept across compaction (added to the summary
message) and restored by `--resume`, from the last TodoWrite call in the
saved conversation.

### The code

**The tool** (`TodoTool`). Like every tool it has a pydantic `Input`, here a
list of `TodoItem`s:

```python
Status = Literal["pending", "in_progress", "completed"]

class TodoItem(BaseModel):
    content: str = Field(min_length=1)
    status: Status
    active_form: str | None = Field(None, alias="activeForm")
```

- `Literal[...]` means "one of exactly these strings". pydantic rejects
  `"done"`, and the model gets the validation error back and fixes its call.
- `min_length=1` rejects empty tasks.
- `alias="activeForm"`: the JSON uses camelCase (as Claude Code does), the
  Python attribute uses snake_case.

`run()` stores the list in `ctx.todos` and returns it as text. It adds a
note if more than one item is `in_progress`. The REPL draws the checklist
from the `ToolUse` event's input.

**No permission needed.** `read_only = True`, and the permission policy now
allows a read-only tool that has **no target** (no file, no command).
Previously that case fell into "reads outside the project" and asked.

**Kept across compaction.** `compact(..., todos=self.ctx.todos)` appends
"The current todo list (TodoWrite): ☑ … ► … ☐ …" to the summary message, so
the model still sees its plan even when the TodoWrite calls were summarised
away.

**Restored on resume.** `last_todos(messages)` walks the conversation
backwards, finds the latest `TodoWrite` call, and parses its arguments with
the same repair-tolerant `parse_tool_arguments` the agent uses.

**Telling the model when to use it.** One line in the system prompt: "For
tasks with 3 or more steps, keep a todo list with TodoWrite …". The tool
description repeats it, and says not to use it for simple one-step requests.

**Reminding the model.** On Windows, Qwen ignored that line: it did the
whole task without ever calling TodoWrite, so no checklist appeared. Claude
Code solves this with *system reminders*, and cmcoder does the same. In
`Agent.run`, after a step's tool calls, `_needs_todo_reminder` checks:

- 3 or more tool calls this turn (`TODO_REMINDER_AFTER`);
- no TodoWrite call this turn, and no open (not completed) todo list;
- the reminder hasn't been sent this turn yet.

If so, `TODO_REMINDER` (a short `<system-reminder>…</system-reminder>`
text) is appended to the content of the last tool result. It goes there,
not in a new user message, because the next request must still end with the
tool results the model asked for. The text says "ignore this if the task is
finished" and "don't mention this reminder", so it doesn't leak into replies.

### New Python ideas

- **`Literal` types** for a fixed set of values, checked by pydantic.
- **Field aliases** (`alias="activeForm"`) and `model_dump(by_alias=True,
  exclude_none=True)` to write it back in camelCase without empty fields.
- **Searching backwards**: `for m in reversed(messages): for c in
  reversed(m.tool_calls): …` finds the most recent call first.

### The tests

`tests/test_todo.py`:
- the tool stores the list and returns a summary (`1/3 done · now: Fixing the bug`);
- it warns about two items in progress;
- invalid statuses and empty tasks are rejected;
- no permission is needed in `default` or `plan` mode;
- in a real agent run there's no prompt, the label is `TodoWrite(1/3 done)`,
  and after `/compact` the summary contains the list;
- resume restores the latest list.

`tests/test_repl_pty.py::test_todo_list_is_shown_as_a_checklist` checks
the ☑ ► ☐ display and `/todos` in a real terminal.

### Try it

Ask cmcoder for something with several steps ("add a `--verbose` flag, use
it in two places, and update the README"), and watch the checklist update.
Then type `/todos`.

## 9. Small/fast model jobs

*Done.* Code: `src/cmcoder/core/titles.py` (new), `Agent._start_title` and
`Agent.close` (`core/agent.py`); compaction summaries from item 1
(`core/compaction.py`, `build_summarizer` in `cli/factory.py`). Tests:
`tests/test_titles.py`, plus the small-model tests in `tests/test_compaction.py`.

### The problem

Some jobs don't need the big model: writing a conversation title, or a
summary for compaction. Sending them to Qwen3-27B wastes GPU time on the
gateway, and the main model's capacity is better spent on the coding task.

### The idea

Your gateway also serves a small model (Qwen3 ~7B). Set it once:

```json
{ "model": "corp:Qwen3.6-27B", "smallFastModel": "corp:Qwen3-7B" }
```

and cmcoder uses it for side jobs:

| Job | When | Fallback if it fails |
|---|---|---|
| Compaction summary (item 1) | the window is 80% full, or `/compact` | the main model, then dropping old tool output |
| Session title | after the first turn | the start of your first message |

Titles appear in `/resume` instead of a cut-off first message: "Fix the login
bug" instead of "the login page rejects valid passwords, please fix…".

### The code

**Running a job in the background** (`Agent._start_title`). The title isn't
needed right away, so nothing should wait for it:

```python
task = asyncio.get_running_loop().create_task(job())
self._background.add(task)
task.add_done_callback(self._background.discard)
```

- `create_task` starts the coroutine running **alongside** whatever happens
  next. In the REPL that's you typing your next message.
- The task is kept in a `set` so Python doesn't garbage-collect it before it
  finishes. When it's done, `add_done_callback` removes it.
- `job()` catches every exception: a title is a nicety, and a failure must
  never disturb the session.

**Finishing up** (`Agent.close`). With `cmcoder -p` the program ends right
after the answer, so `close()` gives a pending title a moment to finish:

```python
_done, pending = await asyncio.wait(self._background, timeout=TITLE_WAIT_ON_CLOSE)
for t in pending:
    t.cancel()
```

`asyncio.wait(..., timeout=2.0)` returns after 2 seconds at most, with the
tasks split into finished and pending. Pending ones are cancelled.

**Asking for a title** (`make_title`). A tiny request: a system prompt
("Write a short title (3 to 7 words)… Reply with the title only"), your first
message (at most 2,000 characters), `thinking=False` so Qwen doesn't think
first, and `max_tokens=40`. Small models still add quotes, "Title:" labels or
a full stop, so `clean_title` strips those with a regular expression. The
title is saved as a `title` record in the session file (item 6).

### New Python ideas

- **Background tasks**: `create_task`, keeping a reference, `add_done_callback`.
- **`asyncio.wait(tasks, timeout=…)`**: wait for several tasks with a time limit.
- **Closures**: `job()` is defined inside `_start_title` and uses its
  `summarizer`, `session` and `first_message` variables. They were copied to
  local names first, so a later `/clear` can't swap the session underneath.

### The tests

`tests/test_titles.py`:
- `clean_title` on real-world model output: quotes, "Title:", markdown bold,
  a blank reply, an over-long one;
- with a small model: exactly one title request after two turns, to the
  small model, with thinking off and `max_tokens` 40, and `/resume` shows the
  title;
- without a small model: no title request, and the first message is the title;
- a failing small model: the turn still succeeds, with the fallback title;
- no session (`persistSessions: false`): no title request.

### Try it

Add `"smallFastModel"` to your settings, have a short conversation, `/exit`,
start `cmcoder` again and type `/resume`.

## 10. Tool-call robustness

*Done.* Code: `src/cmcoder/providers/text_tools.py` (new), the prompted
branch in `OpenAICompatProvider.build_request` and `no_tool_support`
(`providers/openai_compat.py`), the `Holdback` / `extract` / auto-switch
code in `Agent.run` and the `ast.literal_eval` fallback in
`parse_tool_arguments` (`core/agent.py`), `closest_match` (`tools/files.py`),
and doctor's tool-calling check (`cli/doctor.py`). Tests:
`tests/test_text_tools.py`.

### The problem

Native tool calling needs the **backend** to cooperate. vLLM only turns
Qwen's output into `tool_calls` when it's started with
`--enable-auto-tool-choice --tool-call-parser hermes`. Behind your LiteLLM
gateway there are three possible setups:

| Backend setup | What cmcoder receives | Before | Now |
|---|---|---|---|
| parser on | proper `tool_calls` | works | works |
| parser off, `tools` accepted | the call as **text**: `<tool_call>{…}</tool_call>` | the agent stopped, showing raw JSON | parsed and run |
| `tools` rejected (400 "auto tool choice requires --enable-auto-tool-choice") | an error | the turn failed | switches to **prompted** tool calls and retries |

Smaller problems too: models sometimes write Python-style arguments
(`{'path': 'a', 'all': True}`), and an `Edit` whose `old_string` is slightly
off made the model guess blindly.

### The code

**Reading text tool calls** (`extract`). Qwen's chat template writes calls
like this:

```
<tool_call>
{"name": "Read", "arguments": {"file_path": "app.py"}}
</tool_call>
```

`extract(content, known_tools)` finds these blocks with a regular expression
and turns each one into a `ToolCall`. It's careful:
- only **known tool names** with **valid JSON** count, so prose that mentions
  `<tool_call>` is left alone;
- a missing closing tag (the model stopped at its stop token), a
  `"parameters"` key instead of `"arguments"`, and trailing commas are all
  accepted.

The agent applies it whenever a reply has no native tool calls but contains
the tag.

**Keeping the JSON off the screen** (`Holdback`). The reply streams in chunk
by chunk, and the tag can be split across chunks (`"…<to"` + `"ol_call>…"`).
`Holdback.feed()` passes text through but holds back anything that might be
the start of `<tool_call>`. Once the tag is confirmed, it holds everything
after it. `flush()` at the end releases held text that turned out not to be
a tag (like `"a < b"`), so nothing real is lost.

**Prompted mode** (`to_prompted_wire`). When `toolCalling` is `"prompted"`
in the model profile, no `tools` parameter is sent at all:
1. The tool list is added to the system prompt in Qwen's own format: a
   `<tools>…</tools>` block plus "return a json object … within
   `<tool_call></tool_call>` XML tags".
2. Earlier tool calls in the history are written back as `<tool_call>` text.
3. Tool results become a user message of `<tool_response>…</tool_response>`
   blocks. Several results from one step go into one message.

The model answers with text tool calls, which `extract` reads, so the rest
of the agent doesn't know the difference.

**Switching automatically** (`no_tool_support`). If the server rejects the
request with a "tool choice requires --enable-auto-tool-choice"-style
error, the agent switches the profile to prompted for the rest of the
session, warns you once, and retries the step. To skip the failed first try,
set it in settings:

```json
"modelProfiles": [{"match": "qwen3*", "toolCalling": "prompted"}]
```

**Python-style arguments** (`parse_tool_arguments`). If JSON parsing fails,
`ast.literal_eval` tries reading the text as a Python literal. It's safe:
it only evaluates literals (strings, numbers, `True`/`False`/`None`, lists,
dicts), never code.

**Edit hints** (`closest_match`). When `old_string` isn't in the file, the
error now shows the most similar block of lines:

```
old_string was not found in m.py. The closest text is at line 3; check
indentation, quotes and spelling against it:
def total(items):
```

It slides a window the size of `old_string` over the file and scores each
position with `difflib.SequenceMatcher` (with `quick_ratio` as a cheap
pre-filter). Indentation is ignored in the comparison, and nothing is
suggested below a similarity of 0.6.

**Edit-format variants.** The plan listed other edit formats (search/replace
blocks, whole file) "if evals show Edit failures". The evals haven't shown
any yet, so they aren't built. The closest-match hint covers the usual
failure: a slightly wrong `old_string`.

**`doctor`** now says which case your gateway is in:
- tool calls as text: a warning, because cmcoder handles it, but the backend
  parser is better;
- `tools` rejected: a warning explaining the automatic switch and the
  setting.

### New Python ideas

- **`ast.literal_eval`**: safely read Python literals (never `eval` on model output!).
- **`difflib.SequenceMatcher`**: similarity between two strings (0 to 1).
- **The walrus operator in conditions**: `if shown := holdback.feed(text):`
  assigns and tests in one step.
- **A small state machine**: `Holdback` remembers whether it is `inside` a tag
  and what it's holding.

### The tests

`tests/test_text_tools.py`:
- **Parsing:** several calls, prose that mentions the tag, unknown tools,
  broken JSON, a missing closing tag, `"parameters"`, arguments given as a
  string;
- **`Holdback`:** a tag split across chunks; a `<` that isn't a tag;
- **Arguments:** Python-style dicts;
- **Prompted wire format:** the tools block, calls written as text, results
  merged into one message;
- **End to end with the mock server:**
  - text tool calls in native mode (the JSON never reaches the screen, and
    the Read runs);
  - full prompted mode (no `tools` parameter, `<tool_response>` sent back);
  - the automatic switch after vLLM's real "requires
    --enable-auto-tool-choice" error;
- **`closest_match`** and the Edit error message.

### Try it

Set `"toolCalling": "prompted"` for your model in `modelProfiles` and run the
evals: `uv run python evals/run.py`. If they pass about as well as with native
tool calling, you know the fallback works on your gateway.

## 11. Textual TUI

*Done (opt-in).* Code: `src/cmcoder/cli/tui.py` (new), `--tui` in
`cli/main.py`, the `ui` setting. Tests: `tests/test_tui.py` (Textual's
headless pilot), plus a start-up test in `tests/test_repl_pty.py`.

### The problem

The classic REPL prints line after line into your terminal's scrollback. That
works, but:
- a long permission preview can still push things around;
- there's no fixed place for status (model, mode, how full the context is);
- the input line and the output share one stream of text.

### The idea

A **full-screen** terminal app, like `htop` or `vim`, with three fixed areas:

```
┌──────────────────────────────────────────────────────┐
│ conversation (scrolls)                               │
│ > fix the failing test                               │
│ ● Read(tests/test_calc.py)                           │
│   └ Read 40 lines                                    │
│ The test expects …                                   │
├──────────────────────────────────────────────────────┤
│ Ask cmcoder…  (/help for commands)                   │  input
├──────────────────────────────────────────────────────┤
│ Qwen3.6-27B · mode: default · context 41% · Shift+Tab│  status bar
└──────────────────────────────────────────────────────┘
```

Permission requests open a **dialog** of fixed size. The preview has its own
scrollbar, and the buttons (`1 Yes`, `2 Always`, `3 No`) are always at the
bottom. This finishes item 2: however long the command, the options can't be
pushed off screen.

Start it with `cmcoder --tui`, or set `"ui": "textual"` in settings. It's
**opt-in for now**: the classic REPL stays the default until the TUI has
been tried on your Windows terminals. `/resume` and `/rewind` are classic-only
for the moment; everything else (`/help`, `/clear`, `/compact`, `/mode`,
`/model`, `/cost`, `/todos`, Ctrl+C, Shift+Tab) works in both.

### The code

[Textual](https://textual.textualize.io/) builds terminal apps out of
**widgets** (`Input`, `Static`, `Markdown`, `Button`), arranges them with
**containers** (`VerticalScroll`, `Horizontal`), styles them with a CSS-like
language, and runs on `asyncio`, like cmcoder.

**The app** (`CmcoderApp`):

```python
def compose(self) -> ComposeResult:
    yield VerticalScroll(id="log")       # the conversation
    yield Input(placeholder="Ask cmcoder…", id="prompt")
    yield Static(id="status")            # the status bar
```

`compose` describes the screen once; widgets are added to `#log` as events
arrive (`log.mount(Static(...))`).

**Event-driven.** Nothing runs in a `while True` loop that waits for input.
Instead, Textual calls your methods when things happen:
- `@on(Input.Submitted, "#prompt")` → you pressed Enter;
- `Binding("ctrl+c", "interrupt", priority=True)` → calls `action_interrupt`
  (`priority=True` so it wins over Textual's own Ctrl+C);
- `@on(Button.Pressed)` in the dialog.

**Running the agent without freezing the screen** (`run_worker`). A turn can
take a minute, and the UI must stay responsive meanwhile (scrolling, Ctrl+C).
So each turn runs as a **worker**, a background task Textual manages:

```python
self.turn = self.run_worker(self.stream(self.agent.run(prompt)), exclusive=True, group="turn")
```

`stream()` loops over the agent's events (the same events the classic REPL
and `-p` use) and renders each one. Ctrl+C calls `self.turn.cancel()`, which
raises `CancelledError` inside the agent, exactly like the classic REPL.

**Asking permission from inside the worker** (`ask`):

```python
async def ask(self, req):
    return await self.push_screen_wait(PermissionScreen(req, source, lexer))
```

`push_screen_wait` shows the dialog on top and **waits** until it's closed
with `self.dismiss(PermissionAnswer(...))`. The agent simply awaits its `ask`
callback, as before, and has no idea a different UI is answering.

**The dialog layout** is the CSS that fixes item 2 for good:

```css
#dialog  { width: 92%; height: 85%; }
#preview { height: 1fr; }          /* takes the space that's left, scrolls inside */
#buttons { height: auto; }         /* always fully shown */
```

**Streaming Markdown.** Replies arrive in small pieces. Re-rendering Markdown
for every piece would be slow, so the `Markdown` widget is updated at most
every 0.08 s (`UPDATE_EVERY`), plus once at the end.

### New Python ideas

- **Event-driven programming**: handlers (`@on`, `action_*`) instead of a main loop.
- **Workers**: long-running async work next to a live UI, with cancellation.
- **`push_screen_wait`**: a modal dialog that you can `await` like a function call.
- **Testing a UI headlessly**: `async with app.run_test(size=(80, 24)) as
  pilot:` runs the app without a real terminal; `pilot.press("1")` types keys.

### The tests

`tests/test_tui.py` (Textual's pilot, no terminal needed, runs on every OS):
- a reply is shown, and the status bar shows the mode;
- **the item 2 case**: a 500-line command at 80×24. The buttons lie inside
  the 24 rows, the preview scrolls through all of it, and `3` + feedback
  sends "use pytest" back to the model;
- `1` allows and the file is written;
- Shift+Tab cycles the mode, Ctrl+C interrupts `sleep 30`, and `/help` and
  `/mode plan` work;
- the TodoWrite checklist.

`tests/test_repl_pty.py::test_tui_starts_and_answers` starts the real
`cmcoder --tui` in a pseudo-terminal.

### Try it

`cmcoder --tui` in Windows Terminal. Try a long command, the permission
dialog, Ctrl+C and Shift+Tab. If it works well for a while, set `"ui":
"textual"` to make it your default and tell me: then it can become the
default for everyone.

## 12. More evals

*Done.* Code: 13 new folders under `evals/tasks/`, the tool-choice scoring in
`evals/run.py` (item 4). Tests: `tests/test_evals.py`.

### The problem

Five tasks (seven after item 4) can't tell you much about a model. One lucky
or unlucky run moves the score by 15–20%, and whole kinds of work weren't
covered at all: editing several files, writing tests, JSON config, command-line
flags, Windows-safe paths.

### The idea

Twenty small tasks, each a tiny repository plus a prompt and a **check**: a
shell command that exits 0 only if the work was done right.

| Task | What it exercises |
|---|---|
| `add-function`, `implement-fizzbuzz` | writing code from a description or tests |
| `fix-off-by-one`, `fix-type-bug`, `fix-import-error`, `fix-csv-parsing` | finding and fixing bugs (run, read, edit, re-run) |
| `run-tests-and-fix` | test-driven repair |
| `add-test` | writing tests that pass |
| `rename-symbol`, `multi-file-edit` | changes across several files |
| `extract-function`, `use-pathlib` | refactoring without changing behaviour |
| `add-cli-flag` | argparse, checked by running the program |
| `update-json-config` | editing data files exactly (in `acceptEdits` mode) |
| `add-docstring` | documentation, checked with `__doc__` |
| `create-file`, `inspect-files` | tool choice: Write/Grep instead of Bash (item 4) |
| `answer-question`, `count-todos`, `explain-code` | read-only questions in `plan` mode |

Every task also has a `mock_script.json`: replies that solve the task with
real tool calls (Read, then Edit with the exact text, then run the tests). In
CI, `evals/run.py --mock` plays them against cmcoder on Linux, macOS and
Windows. That checks the whole harness (tools, permissions, checks) without a
model. Against your gateway, the same tasks measure Qwen.

### The code

The tasks were generated by a small Python script rather than typed by hand.
The JSON escaping in check commands is error-prone, and one slip proved it.
The `extract-function` check compared prices like `'$12.05'` inside a bash
double-quoted string, where `$1` and `$0` are shell variables, so the check
failed even on a correct solution. The fix is `\$`. The lesson: **test your
tests**.

That's what `tests/test_evals.py` does, for every task:
- `task.json`, the mock script and `repo/` are well formed;
- **the check fails on the untouched repository.** A check that passes
  before any work is done measures nothing. Each task is copied to a temp
  folder and its check is run with bash, and it must exit non-zero.

Two Windows details are built in:
- Checks use `"$PYTHON"`, the exact interpreter running the evals, because
  `python3` usually doesn't exist on Windows.
- Checks that compare output lines strip `\r` first (`tr -d '\r'`), because
  Python on Windows ends lines with `\r\n`.

### New Python ideas

- **Generating test data with code**: build dicts in Python, then
  `json.dumps` writes correctly escaped JSON.
- **`pytest.mark.parametrize` over files**: one test per task folder
  (`ids=lambda p: p.name` names them).
- **Negative controls**: checking that a check *can* fail.

### The tests

`tests/test_evals.py`: at least 20 tasks; each well-formed; each check fails
before the work is done (41 tests). `uv run python evals/run.py --mock` →
20/20.

### Try it

Against your gateway:

```bash
uv run python evals/run.py                      # all 20 with your configured model
uv run python evals/run.py --task add-cli-flag  # just one
```

The last two lines give the pass rate and the tool-choice share. Those two
numbers are the Phase 1 sign-off.
