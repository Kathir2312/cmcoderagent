# cmcoder repository notes

- Python 3.11+ package in `src/cmcoder`; managed with `uv`.
- Run checks before finishing a change:
  - `uv run ruff check src tests evals && uv run ruff format --check src tests evals`
  - `uv run pyright`
  - `uv run pytest -q`
  - `uv run python evals/run.py --mock`
- Layout: `providers/` (OpenAI-compatible adapter, auth, TLS, model profiles), `core/` (agent loop,
  permissions, prompt), `tools/` (Read/Write/Edit/Glob/Grep/Bash), `cli/` (entry point, REPL, headless,
  doctor), `protocol/` (events shared with front ends), `testing/` (mock server).
- Never disable TLS verification. Never log or persist API keys outside `providers/auth.py`.
- Tests must not need a real model: use `cmcoder.testing.mock_server`.
- `docs/DESIGN.md` is the source of truth for architecture decisions; update it when a decision changes.
