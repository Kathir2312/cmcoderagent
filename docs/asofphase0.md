# cmcoder code walkthrough, as of Phase 0 (for a day-1 Python developer)

This guide explains how cmcoder works, file by file, assuming you know basic
Python (variables, functions, `if`/`for`, lists and dicts) and nothing else.
Every new idea is explained the first time it appears. Read it from top to
bottom with the code open next to it.

Contents:

1. [What cmcoder does, in one picture](#1-what-cmcoder-does-in-one-picture)
2. [Python ideas you'll meet](#2-python-ideas-youll-meet-in-this-code)
3. [The folder layout](#3-the-folder-layout)
4. [Follow one request from start to finish](#4-follow-one-request-from-start-to-finish)
5. [File-by-file tour](#5-file-by-file-tour)
6. [How the tests work](#6-how-the-tests-work)
7. [Your first change: adding a tool](#7-your-first-change-adding-a-tool)
8. [Glossary](#8-glossary)

---

## 1. What cmcoder does, in one picture

A language model (Qwen3, on your company's server) can only **write text**. It
can't open your files or run commands. cmcoder is the program on your laptop
that gives the model "hands":

```
 You type: "fix the failing test"
        │
        ▼
 ┌──────────────── cmcoder (on your laptop) ────────────────┐
 │  1. Send your message + the list of tools to the model    │
 │  2. Model replies: "call the Bash tool with `pytest`"     │◄──── HTTPS ────► Qwen3 model
 │  3. cmcoder asks you for permission, runs `pytest`        │      (LiteLLM gateway)
 │  4. Send the test output back to the model                │
 │  5. Model replies: "call Edit on calc.py: …"              │
 │  6. … repeat until the model answers with plain text …    │
 └───────────────────────────────────────────────────────────┘
        │
        ▼
 You see: "Fixed the off-by-one in calc.py:2; tests pass."
```

That repeat-until-done cycle is called the **agent loop**. Almost everything
else in the code exists to make that loop safe (permissions), reliable (error
handling, retries), and pleasant (the terminal UI).

Two words to remember:

- **Tool call**: the model's request to use one of cmcoder's tools, such as
  "Read file `calc.py`". The model can't do it itself; cmcoder does it and
  sends back the result.
- **Turn**: one user message plus everything the agent does until it answers.

---

## 2. Python ideas you'll meet in this code

You'll see these on almost every page. Skim now, come back when needed.

### Type hints

```python
def add(a: int, b: int) -> int:
    return a + b
```

`a: int` means "a should be an int"; `-> int` means "returns an int". Python
does **not** enforce them at runtime; they're notes for humans and for the
`pyright` checker, which reads them and warns about mistakes. You'll also see:

- `str | None`: "a string, or `None`".
- `list[str]`: a list of strings. `dict[str, int]`: keys are strings, values ints.
- `from __future__ import annotations` at the top of files: lets us write
  these hints freely. You can ignore it.

### Classes and `self`

A class bundles data and the functions that work on it:

```python
class Counter:
    def __init__(self, start: int) -> None:   # runs when you create one
        self.value = start                    # data stored on the object

    def bump(self) -> None:                   # a "method"
        self.value += 1

c = Counter(5)
c.bump()        # c.value is now 6
```

### `@dataclass`: classes that just hold data

```python
from dataclasses import dataclass

@dataclass
class ToolResult:
    content: str
    is_error: bool = False
```

The `@dataclass` line (a **decorator**, which modifies the thing below it)
writes `__init__` for you, so `ToolResult("hello", is_error=True)` just works.
See `providers/messages.py`, which is almost all dataclasses.

### pydantic models: classes that validate data

```python
from pydantic import BaseModel

class ReadInput(BaseModel):
    file_path: str
    limit: int | None = None

ReadInput.model_validate({"file_path": "a.py"})       # OK
ReadInput.model_validate({"limit": "x"})               # raises ValidationError
```

We use pydantic wherever data comes **from outside** and might be wrong: the
settings file, and the arguments the model sends for a tool call. It checks the
types and gives clear error messages, and `ReadInput.model_json_schema()`
produces the description of the tool's arguments that we send to the model.

### `async` / `await`: doing several waits at once

Talking to a server or running a command means **waiting**. `async def`
functions can pause at `await` while something else runs:

```python
async def fetch():
    response = await client.get(url)   # pause here until the server answers
    return response
```

You can only `await` inside an `async def`. The program starts the whole thing
with `asyncio.run(...)` (see `cli/main.py`). Why we need it: while the model is
streaming its answer, we also want to show it on screen and react to Ctrl+C.

### Generators and `yield`: producing results one at a time

A function with `yield` hands back values one by one instead of building a
list:

```python
def count_up(n):
    for i in range(n):
        yield i          # hand back i, pause, continue on the next request

for x in count_up(3):    # 0, 1, 2
    print(x)
```

`async def ... yield` is the async version, consumed with `async for`. The
agent uses this to **stream**: `agent.run()` yields each event (a piece of
text, a tool call, a tool result) the moment it happens, and the screen shows
it immediately instead of waiting for the end.

### Exceptions

```python
try:
    risky()
except ValueError as e:
    print("that went wrong:", e)
```

We define our own exception classes (e.g. `AuthFailed`, `ContextTooLong` in
`providers/openai_compat.py`) so callers can react to each problem
differently.

### Relative imports

`from ..providers.messages import Message` means "from the `providers` package
one level up". Each folder with an `__init__.py` file is a **package**.

### The walrus operator `:=`

`if problem := key_problem(key):` assigns **and** tests in one line: it is
the same as `problem = key_problem(key)` followed by `if problem:`.

---

## 3. The folder layout

```
cmcoderagent/
├── pyproject.toml          project name, dependencies, tool settings
├── README.md               how to install and use cmcoder
├── docs/
│   ├── DESIGN.md           why things are built this way (decisions, roadmap)
│   └── asofphase0.md       this file
├── src/cmcoder/            ← the application
│   ├── __main__.py         lets you run `python -m cmcoder`
│   ├── cli/                the commands you type: cmcoder, login, doctor…
│   ├── core/               the agent loop, permissions, prompt, context
│   ├── providers/          talking to the model server over HTTPS
│   ├── tools/              Read, Write, Edit, Glob, Grep, Bash
│   ├── config/             reading settings files
│   ├── protocol/           the "events" the agent reports to the screen
│   ├── testing/            a fake model server used by tests
│   ├── compat.py           Windows vs Linux/macOS differences
│   └── sensitive.py        which files count as secrets
├── tests/                  automated tests (pytest)
└── evals/                  5 practice tasks to score a real model
```

A good mental model of how the pieces depend on each other (arrows mean
"uses"):

```
cli  ──►  core  ──►  providers   (talk to the model)
               └──►  tools       (do things on your laptop)
     ──►  config                 (read settings)
everything ──► protocol          (shared event types)
```

`core` doesn't know about the terminal, and `providers` doesn't know about
tools. That separation is what will let the VS Code extension (Phase 2) reuse
the same core.

---

## 4. Follow one request from start to finish

The best way to understand the code is to follow what happens when you type
`cmcoder` and ask a question. File names are given so you can open them.

**Step 1: the command starts.** `pyproject.toml` has:

```toml
[project.scripts]
cmcoder = "cmcoder.cli.main:run"
```

So typing `cmcoder` calls the function `run()` in `src/cmcoder/cli/main.py`:

```python
def run() -> None:
    use_utf8_stdio()
    if len(sys.argv) > 1 and sys.argv[1] in SUBCOMMANDS:
        sub_app()      # cmcoder doctor / login / models …
    else:
        main_app()     # cmcoder  or  cmcoder -p "…"
```

`sys.argv` is the list of words you typed. `main_app` and `sub_app` are built
with **typer**, a library that turns a Python function's parameters into
command-line options (`--model`, `-p`, …).

**Step 2: settings are loaded.** `main()` calls `load_settings()` in
`config/settings.py`. It reads up to three JSON files plus environment
variables and merges them into one `Settings` object (details in §5.2).

**Step 3: the agent is built.** For an interactive session, `main()` creates
`Repl(...)` (from `cli/repl.py`) and runs `Repl.main()`, which calls
`build_agent()` in `cli/factory.py`. `build_agent` is the "assembly line":

1. works out which server and model to use (`settings.resolve_model()`);
2. creates the HTTPS client with your company certificate (`build_provider`);
3. picks the model's profile, e.g. its context size (`resolve_model_profile`);
4. creates the permission policy (`PermissionPolicy`);
5. builds the system prompt, the hidden instructions sent to the model on
   every request (`build_system_prompt`);
6. creates the `Agent` with the six tools from `default_tools()`.

**Step 4: you type a message.** `Repl.main()` reads your line with
prompt_toolkit, then `_run_turn()` starts `agent.run(your_text)` and shows each
event as it arrives (`_render`).

**Step 5: the agent loop runs.** `Agent.run()` in `core/agent.py`:

```
add your message to self.messages
loop:
    check the conversation still fits in the context window   (core/context.py)
    send messages + tool descriptions to the model, streaming  (providers/openai_compat.py)
    show text as it arrives
    if the model asked for no tools → finished, report the result
    for each tool call:
        check the arguments are valid JSON and match the tool's schema
        ask the permission policy: allow / deny / ask the user   (core/permissions.py)
        run the tool                                              (tools/*.py)
        add the result to self.messages
    go round again so the model can read the results
```

**Step 6: a tool runs.** Say the model calls `Read` with
`{"file_path": "calc.py"}`. `ReadTool.run()` in `tools/files.py` opens the
file, numbers its lines, and returns a `ToolResult`. That text goes back to the
model on the next lap of the loop.

**Step 7: the answer is shown.** When the model replies without tool calls,
`run()` yields a `Result` event and the turn ends. The REPL waits for your next
message.

`cmcoder -p "question"` follows the same steps, except step 4 uses
`cli/headless.py`: there's no one to ask for permission, so anything needing
approval is refused with a hint, and only the final answer is printed.

---

## 5. File-by-file tour

Read these in this order; each builds on the ones before.

### 5.1 `providers/messages.py`: the shared vocabulary

Everything that passes between cmcoder and the model is described here, as
simple dataclasses:

- `Message`: one entry in the conversation. `role` is `"system"` (hidden
  instructions), `"user"` (you), `"assistant"` (the model) or `"tool"` (a
  tool's output). An assistant message can carry `tool_calls`.
- `ToolCall`: `id`, `name` (e.g. `"Read"`) and `arguments`, which is a **JSON
  string** written by the model, like `'{"file_path": "calc.py"}'`.
- `ToolSpec`: how we describe a tool to the model: name, description, and a
  JSON schema of its arguments.
- `Usage`: how many **tokens** (roughly word-pieces) the request and the reply
  used.
- The stream events `TextDelta`, `ReasoningDelta`, `ToolCallStarted` and
  `StreamDone`: what the provider yields while the reply arrives piece by
  piece.

Small helpers such as `Message.user("hi")` are **classmethods**: functions you
call on the class itself to build an instance conveniently.

### 5.2 `config/settings.py`: reading settings

Settings come from several places, and later ones win:

1. `~/.cmcoder/settings.json`: your personal settings
2. `<project>/.cmcoder/settings.json`: shared by the team (committed to git)
3. `<project>/.cmcoder/settings.local.json`: yours, for this project only
4. environment variables like `CMCODER_MODEL`

`load_settings()` reads each file with `_read_json()`, combines them with
`deep_merge()` (a recursive function: when both sides have a dictionary under
the same key, it merges the insides too), then turns the result into a
`Settings` pydantic object. If you make a mistake in the JSON, pydantic raises
an error and we turn it into a friendly `SettingsError`.

Worth reading:

- `Settings.resolve_model("corp:Qwen3.6-27B")` splits that into the provider
  name `corp` and the model name. It only treats the part before `:` as a
  provider if a provider with that name exists, because some model names
  contain `:` themselves (`qwen3:8b`).
- `env_api_key_source()` decides which environment variable may supply the
  API key. It deliberately ignores `OPENAI_API_KEY` unless the endpoint also
  came from `OPENAI_BASE_URL`, so an unrelated key is never sent to your
  gateway.
- `Field(..., alias="baseUrl")`: in the JSON file the key is `baseUrl`, but in
  Python we write `base_url`. The alias connects the two.

### 5.3 `providers/`: talking to the model server

**`transport.py`** builds the HTTPS client (from the `httpx` library).
`build_ssl_context()` decides which certificates to trust: normally the
operating system's store (via `truststore`), so a company certificate installed
by IT just works; or, if you set `caCertPath`, Python's default store plus that
file. Certificate checking is never turned off.

**`auth.py`** is about the API key. `store_api_key()` saves it in the
Windows Credential Manager or macOS Keychain (via `keyring`), falling back to a
private file. `ApiKeyAuth.get_headers()` returns the HTTP header
`Authorization: Bearer sk-…` that proves who you are. `mask_key()` turns a key
into `sk-...6cd` so it can be shown in messages without revealing it.

**`profiles.py`**: different models need different settings (how much text
fits, whether thinking can be switched off…). `resolve_profile("Qwen3.6-27B")`
starts from built-in defaults, applies your `modelProfiles` overrides, then any
limits the server reports. `model_size_b()` uses a **regular expression** (a
text-matching pattern from the `re` module) to read "27" out of "27B", so small
models get a shorter prompt.

**`thinking.py`**: Qwen3 can "think out loud" before answering, sometimes
wrapped in `<think> … </think>` inside the text. `ThinkSplitter` separates the
thinking from the answer **while the text streams in**. The tricky part is
that a tag can be split across two pieces, `"<thi"` then `"nk>"`, so the
splitter holds back any ending that might be the start of a tag until the
next piece arrives.

**`openai_compat.py`** is the biggest provider file. Read it in this order:

1. The **error classes** at the top (`AuthFailed`, `BudgetExceeded`,
   `RateLimited`, …), all children of `ProviderError`. Each has a `hint` so
   the user knows what to do.
2. `classify_http_error()` turns a server error (status code + body) into the
   right error class; `classify_transport_error()` does the same for network
   problems (no VPN, bad certificate, timeout).
3. `to_wire_messages()` converts our `Message` objects into the JSON format
   the OpenAI API expects. "Wire format" means "what actually travels over the
   network".
4. `OpenAICompatProvider.build_request()` assembles the request body: model,
   messages, tools, temperature, `max_tokens`, and the Qwen3 thinking switch.
5. `stream_chat()` sends it. It is an **async generator**: it yields events
   as they arrive. The `while True` loop around it retries on temporary
   errors (server busy, rate limit), waiting a little longer each time
   (`_backoff`); it never retries after the answer has started streaming.
6. `_parse_stream()` reads the server's reply line by line. The server sends
   "Server-Sent Events": lines starting with `data: {json}`, ending with
   `data: [DONE]`. Each JSON piece may contain a bit of text, a bit of
   reasoning, or a fragment of a tool call. Tool-call arguments arrive in
   fragments too, so they're collected in `calls` by their `index` and joined
   at the end.

### 5.4 `tools/`: the agent's hands

**`base.py`** defines what every tool looks like:

```python
class Tool(ABC):
    name: ClassVar[str]                # "Read"
    description: ClassVar[str]         # text the model reads to decide when to use it
    Input: ClassVar[type[ToolInput]]   # pydantic model of the arguments
    read_only: ClassVar[bool] = False  # True if it never changes anything

    def spec(self) -> ToolSpec: ...     # builds the description sent to the model
    async def run(self, args, ctx) -> ToolResult: ...   # does the work
    def permission_target(self, args, ctx): ...          # what permission rules look at
    def describe(self, args, ctx) -> str: ...             # short label: "Read(calc.py)"
```

`ABC` means **abstract base class**: you can't use `Tool` directly, only
subclasses that fill in `run()` (marked `@abstractmethod`). `ClassVar` means
"set once on the class, not per object".

`ToolContext` is the shared state every tool receives: the working directory,
the project root, which files have been read (and when), and the running shell.
`ctx.resolve("calc.py")` turns a path the model wrote into a full, absolute
path.

**`files.py`**: `ReadTool`, `WriteTool`, `EditTool`. Things to notice:

- **Read before write.** `ctx.check_fresh(path)` refuses to change a file the
  agent hasn't read, or one that changed since it read it (maybe you edited it
  in your editor). This stops the model overwriting work it hasn't seen.
- **Edit** replaces an exact piece of text. If the text isn't found, or is
  found more than once, it returns an error the model can learn from, instead
  of guessing.
- Read stops at a line boundary when the output gets too long, and tells the
  model which `offset` to continue from.

**`search.py`**: `GlobTool` (find files by name) and `GrepTool` (search
inside files). They use the fast `ripgrep` program if installed, otherwise
`walk_files()` / `python_grep()`, a pure-Python version that follows the same
`.gitignore` rules. Both skip secret files.

**`shell.py` and `bash.py`**: `PersistentShell` keeps **one** bash process
running for the whole session, so `cd` in one command still applies in the
next. The clever part is in `run()`: after your command, it prints a unique
marker line like `__CMCODER_DONE_<random>__0`. Reading the output until that
marker tells us the command has finished, and the number after it is the exit
code. On timeout or Ctrl+C the whole process group is killed
(`compat.kill_process_tree`).

**`registry.py`** just lists the six tools:

```python
def default_tools() -> list[Tool]:
    return [ReadTool(), WriteTool(), EditTool(), GlobTool(), GrepTool(), BashTool()]
```

### 5.5 `core/permissions.py`: what the agent may do

Before any tool runs, `PermissionPolicy.check()` returns one of three
`Decision`s: `ALLOW`, `DENY` or `ASK` (show you a prompt). It checks, in
this order:

1. **Deny rules** from settings, e.g. `"Read(docs/private/**)"`: always win.
2. **Protected paths**: editing `.cmcoder/` or `.git/` always asks, so the
   agent can't give itself permissions or plant a git hook.
3. **Allow rules**, e.g. `"Bash(npm test:*)"`: any command starting with
   `npm test`.
4. **Secret files** (`.env`, `*.pem`…; list in `sensitive.py`): denied.
5. **The mode's defaults**: reading inside the project is fine; edits ask
   unless the mode is `acceptEdits`; commands ask unless they're on the
   read-only list (`is_safe_command`, e.g. `git status`).

One safety detail: `has_shell_operators()` makes sure a rule like
`Bash(npm test:*)` can't approve `npm test; rm -rf ~`. Any `;`, `&&`, `|` or
`$(` in the command means the prefix rule doesn't apply.

`suggest_rule()` builds the rule saved when you answer "Yes, and don't ask
again" (e.g. `Bash(git stash:*)`), and `add_local_allow_rule()` in
`settings.py` writes it to `settings.local.json`.

### 5.6 `core/prompt.py`: what the model is told

`build_system_prompt()` assembles the hidden first message:

1. `BASE_PROMPT`: how to behave ("read a file before editing it", "be
   concise"…). Small models get the shorter `COMPACT_PROMPT`.
2. Your **memory files**: `CMCODER.md` or `AGENTS.md` from your home folder
   and the project, found by `load_memory_files()`. Put project rules there
   ("use pytest", "never touch the migrations folder").
3. Environment facts: folder, OS, date, git branch.

The stable parts come first and the changing parts last. Servers can reuse
work for an identical beginning, which makes replies start faster.

### 5.7 `core/context.py`: fitting in the model's memory

A model can only read a limited amount of text per request: its **context
window**, measured in tokens (Qwen3 on your server: assumed 32,768). Every
request contains the whole conversation so far, so it keeps growing.
`ContextBudget`:

- `estimate()` guesses the size in tokens from the number of characters, and
  `calibrate()` corrects the guess using the real count the server reports
  after each request.
- `max_tokens()` asks the model for no more reply than will still fit.
- `free_space()` runs when the window is nearly full: it replaces the
  **oldest** big tool outputs with a short note ("Older tool output removed…"),
  keeping the most recent ones, so the agent can carry on.

### 5.8 `core/agent.py`: the heart

Now the agent loop will make sense. Key pieces:

- `parse_tool_arguments()`: the model writes tool arguments as JSON text,
  sometimes slightly broken (trailing commas, wrapped in ``` fences). This
  tries to repair it before giving up.
- `Agent.__init__`: stores the provider, model, tools (as a dictionary from
  name to tool), permission policy and the conversation in `self.messages`.
- `Agent.run(prompt)`: the loop from §4, step 5. Look for `while True:`. Each
  lap is one model call (`steps` counts them, and `max_turns` stops runaway
  loops). It ends by yielding a `Result` with `subtype` `"success"`,
  `"error"`, `"max_turns"` or `"interrupted"`.
- `Agent._run_call(call, repeated)`: handles **one** tool call: unknown tool?
  bad JSON? wrong arguments? same call 3 times in a row? Then permission, then
  `tool.run()`. Every outcome, success or error, becomes a tool message,
  because the model must always get an answer to each call it made.
- `_repair_after_interrupt()`: if you press Ctrl+C mid-turn, this adds
  "Interrupted by the user" answers for any unanswered tool calls, so the
  conversation stays valid for your next message.

Notice that `run()` never prints anything. It only `yield`s events. Whoever
calls it (the terminal UI, the `-p` mode, or later the VS Code extension)
decides how to show them.

### 5.9 `protocol/events.py`: what the agent reports

Each event type is a small pydantic class with a `type` field:
`AssistantDelta` (a piece of text), `ToolUse`, `ToolResult`,
`PermissionDenied`, `UsageUpdate`, `Warning`, `Error`, `Result`. Because they
are pydantic, `event.model_dump_json()` turns any of them into JSON. That's
exactly what `cmcoder -p "…" --output-format stream-json` prints, and what the
VS Code extension will read in Phase 2.

### 5.10 `cli/`: what you type

- **`main.py`**: the commands. `main()` handles `cmcoder` and `cmcoder -p`;
  `doctor()`, `login()`, `logout()`, `models()` are the subcommands. Each is a
  plain function; typer reads its parameters (with `Annotated[...,
  typer.Option(...)]`) to build `--options`.
- **`factory.py`**: builds the agent (§4, step 3). Also `cached_model_info()`,
  which remembers the server's model limits for a day in
  `~/.cmcoder/cache/`.
- **`repl.py`**: the interactive screen. `_render()` is one big `if/elif` that
  decides how to draw each event type (with the `rich` library). `ask()` shows
  the permission prompt with a diff preview. `_command()` handles `/help`,
  `/model`, `/mode`, `/clear`, `/cost`.
- **`headless.py`**: the `-p` mode. It runs the same agent with `ask=None`, so
  anything needing approval is refused with a hint about `--allowedTools`.
- **`doctor.py`**: `cmcoder doctor`. Each check calls `self.report(OK/WARN/FAIL,
  …)`. `check_network()` tests DNS → TCP → TLS step by step, so you learn
  exactly which layer is broken; `probe_model()` sends three small real
  requests (plain reply, thinking off, tool call).

### 5.11 `compat.py`: Windows vs macOS/Linux

All the "if Windows do X, else do Y" lives here so the rest of the code
doesn't have to care:

- `find_shell()`: finds Git Bash on Windows, skipping the WSL `bash.exe`
  that can't see your files.
- `kill_process_tree()`: `taskkill /T` on Windows, `killpg` elsewhere.
- `stdin_has_data()`: detects `git diff | cmcoder -p "review"`.
- `InterruptHandler`: routes Ctrl+C to "cancel the current turn".
- `from_shell_path()`: accepts `/c/Users/…` (Git Bash style) as `C:/Users/…`.

### 5.12 `testing/mock_server.py`: a pretend model

A tiny web server that **acts like** the real model server but replies from a
script you give it:

```python
script = [
    {"tool_calls": [{"name": "Read", "arguments": {"file_path": "calc.py"}}]},
    {"content": "Fixed it."},
]
```

The first request gets the first reply, the second gets the second, and so on.
It also records every request in `server.requests`, so tests can check exactly
what cmcoder sent. That's how the whole agent is tested without a GPU or an API
key.

---

## 6. How the tests work

Tests live in `tests/` and run with `uv run pytest -q`. A test is a function
whose name starts with `test_` and that uses `assert` to check something:

```python
def test_model_size_parsing() -> None:
    assert model_size_b("qwen3-27b") == 27
```

If an `assert` is false, pytest reports that test as failed. Other things to
know:

- **Fixtures** (in `tests/conftest.py`) are reusable setups that tests ask for
  by naming them as parameters. `project` gives a fresh empty project folder;
  `mock_server` starts a fake model server. The autouse `_isolate_config`
  fixture makes sure tests never touch your real `~/.cmcoder` or keychain.
- `async def test_...` works because of the `pytest-asyncio` plugin.
- `monkeypatch` temporarily replaces something for one test, e.g. pretending
  ripgrep isn't installed: `monkeypatch.setattr("cmcoder.tools.search.ripgrep",
  lambda: None)`.

| Test file | What it checks |
|---|---|
| `test_tools.py` | each tool on real temporary files (both ripgrep and the fallback) |
| `test_permissions.py` | allow/deny/ask decisions, including attempts to sneak past rules |
| `test_agent.py` | the full agent loop against the mock server |
| `test_openai_compat.py` | streaming, tool-call assembly, retries, errors |
| `test_cli.py` | runs the real `cmcoder` command as a separate process |
| `test_tls.py` | creates a private "company" certificate authority and checks trust |
| `test_repl_pty.py` | types into the interactive UI through a fake terminal (Linux/macOS) |
| `test_litellm_integration.py` | through a real LiteLLM proxy (only if installed) |

The **evals** (`evals/run.py`) are different from tests: they give a real
model a small task (e.g. "fix the failing test") in a copy of a tiny repo,
then check the result. With `--mock` they replay scripted answers, which only
proves cmcoder's plumbing works. Without it, they measure your real model.

---

## 7. Your first change: adding a tool

A good exercise. Let's add `LineCount`, which counts the lines in a file.

**1. Write the tool** in a new file `src/cmcoder/tools/linecount.py`:

```python
"""LineCount tool: how many lines a file has."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from .base import Tool, ToolContext, ToolInput, ToolResult


class LineCountInput(ToolInput):
    file_path: str = Field(description="Path of the file to count.")


class LineCountTool(Tool):
    name = "LineCount"
    description = "Count the lines in a text file."
    Input = LineCountInput
    read_only = True                      # it never changes anything

    def permission_target(self, args: LineCountInput, ctx: ToolContext) -> Path:
        return ctx.resolve(args.file_path)    # so secret-file rules apply

    def describe(self, args: LineCountInput, ctx: ToolContext) -> str:
        return f"LineCount({args.file_path})"

    async def run(self, args: LineCountInput, ctx: ToolContext) -> ToolResult:
        path = ctx.resolve(args.file_path)
        try:
            n = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
        except OSError as e:
            return ToolResult(f"Cannot read {path}: {e}", is_error=True)
        return ToolResult(f"{n} lines", summary=f"{n} lines")
```

**2. Register it** in `tools/registry.py`: import it and add
`LineCountTool()` to the list.

**3. Test it** in `tests/test_tools.py`:

```python
async def test_line_count(ctx: ToolContext, project: Path) -> None:
    from cmcoder.tools.linecount import LineCountInput, LineCountTool

    (project / "a.txt").write_text("one\ntwo\n")
    res = await LineCountTool().run(LineCountInput(file_path="a.txt"), ctx)
    assert res.content == "2 lines"
```

**4. Check everything:**

```
uv run ruff check src tests      # style
uv run pyright                   # type hints
uv run pytest -q                 # tests
```

That's the whole pattern: a pydantic input class, a `Tool` subclass with
`run()`, one line in the registry, and a test. The model now sees the new tool
in every request, and permissions, the UI and the `-p` mode work with it
automatically.

---

## 8. Glossary

| Term | Meaning |
|---|---|
| **Agent loop** | Model → tool calls → results → model … until it answers in plain text |
| **API key** | A secret string proving who you are to the server (`sk-…`) |
| **Context window** | The most text a model can read in one request, in tokens |
| **Event** | A small message the agent yields to report progress (text, tool use, result…) |
| **Gateway / LiteLLM** | The company server between you and the models; checks keys, routes requests |
| **JSON schema** | A description of what arguments a tool accepts, sent to the model |
| **Mock** | A fake stand-in used in tests (here, a fake model server) |
| **Profile** | Per-model settings: context size, thinking switch, prompt size… |
| **Provider** | A configured model server (`corp` in your settings) |
| **Reasoning / thinking** | The model's "thinking out loud" before answering; shown separately |
| **REPL** | Read-Eval-Print Loop: the interactive prompt (`>`) |
| **Streaming** | Receiving the reply piece by piece as it is generated |
| **System prompt** | Hidden instructions sent first in every request |
| **TLS / certificate** | What makes HTTPS secure; the company CA signs the server's certificate |
| **Token** | The unit models count text in; roughly ¾ of a word |
| **Tool call** | The model's request for cmcoder to run one of its tools |
| **Turn** | One user message and everything until the agent answers |

Where to go next: `docs/DESIGN.md` explains *why* things are built this way,
and the tests are the best examples of how each piece is used.
