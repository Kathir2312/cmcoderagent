from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from cmcoder.core.agent import Agent, PermissionAnswer, PermissionRequest
from cmcoder.core.permissions import PermissionPolicy
from cmcoder.core.steer import file_work_redirect
from cmcoder.protocol import events as ev
from cmcoder.providers.profiles import resolve_profile
from cmcoder.tools.base import ToolContext
from cmcoder.tools.registry import default_tools

from .conftest import make_provider

REDIRECTED = [
    # writes: the user-trial case and its variants
    ("cat > big.txt << 'EOF'\nrow 1\nEOF", "Write tool", 'file_path="big.txt"'),
    ("cat <<EOF > src/a.py\nx = 1\nEOF", "Write tool", 'file_path="src/a.py"'),
    ("cat >> notes.md << EOF\nmore\nEOF", "Edit tool", "notes.md"),
    ("tee out.txt <<'EOF'\nx\nEOF", "Write tool", "out.txt"),
    ("echo 'hello' > a.txt", "Write tool", "a.txt"),
    ("printf 'x\\n' >> log.txt", "Edit tool", "log.txt"),
    ("python3 -c \"open('b.txt', 'w').write('x')\"", "Write tool", "b.txt"),
    ("sed -i 's/a/b/' x.py", "Edit tool", "sed -i"),
    # reads
    ("cat src/app.py", "Read tool", 'file_path="src/app.py"'),
    ("cat -n src/app.py", "Read tool", "src/app.py"),
    ("head -n 50 src/app.py", "Read tool", "limit=50"),
    ("head -20 x.py", "Read tool", "limit=20"),
    ("tail -n 30 log.txt", "Read tool", "log.txt"),
    ("sed -n '10,40p' src/app.py", "Read tool", "offset=10, limit=31"),
    ("python -c \"print(open('a.py').read())\"", "Read tool", "a.py"),
    # searches
    ("grep -rn 'def main' src", "Grep tool", 'pattern="def main", path="src"'),
    ("grep foo a.py", "Grep tool", 'path="a.py"'),
    ("rg TODO", "Grep tool", 'pattern="TODO"'),
    ("find . -name '*.py'", "Glob tool", 'pattern="**/*.py"'),
    ("find src -name '*.ts'", "Glob tool", 'pattern="src/**/*.ts"'),
]

NOT_REDIRECTED = [
    "pytest -q",
    "git diff",
    "ls -la",
    "npm run build",
    "cat a.py | grep x",  # pipelines run as usual
    "cat a b",  # several files
    "cat <<EOF\nhello\nEOF",  # heredoc to stdout, not a file
    "python3 - << 'EOF'\nprint(1)\nEOF",  # a script fed to python
    "echo hi > /dev/null",
    "echo hi 2> err.txt",
    "tail -f log.txt",
    "sed 's/a/b/' x.py",  # prints, doesn't change the file
    "grep -A 3 foo a.py",  # option values would be misread
    "rg -t py TODO",
    "find . -name '*.pyc' -delete",
    "make && cat out.txt",
]


@pytest.mark.parametrize(("command", "tool", "detail"), REDIRECTED)
def test_file_work_is_redirected(command: str, tool: str, detail: str) -> None:
    hint = file_work_redirect(command)
    assert hint and hint.startswith("Not run:"), command
    assert tool in hint and detail in hint, hint


@pytest.mark.parametrize("command", NOT_REDIRECTED)
def test_other_commands_run(command: str) -> None:
    assert file_work_redirect(command) is None, file_work_redirect(command)


# --- in the agent ------------------------------------------------------------------


def make_agent(server: Any, project: Path, ask: Any = None, steer: bool = True) -> Agent:
    profile = resolve_profile("qwen3-27b", overrides=[{"steerBashFileWork": steer}])
    return Agent(
        make_provider(server),
        "qwen3-27b",
        profile,
        default_tools(),
        PermissionPolicy("acceptEdits"),
        ToolContext(cwd=project, project_root=project),
        "You are a test agent.",
        ask=ask,
    )


async def run(agent: Agent, prompt: str) -> list[ev.Event]:
    try:
        return [e async for e in agent.run(prompt)]
    finally:
        await agent.close()


HEREDOC = "cat > notes.md << 'EOF'\n# Notes\nEOF"


async def test_heredoc_is_answered_without_a_prompt(mock_server: Any, project: Path) -> None:
    """The user-trial case: no permission prompt, the model is told to use
    Write, and does."""
    server = mock_server(
        [
            {"tool_calls": [{"name": "Bash", "arguments": {"command": HEREDOC}}]},
            {
                "tool_calls": [
                    {
                        "name": "Write",
                        "arguments": {"file_path": "notes.md", "content": "# Notes\n"},
                    }
                ]
            },
            {"content": "Created notes.md."},
        ]
    )
    asked: list[PermissionRequest] = []

    async def ask(req: PermissionRequest) -> PermissionAnswer:
        asked.append(req)
        return PermissionAnswer(allow=False)

    events = await run(make_agent(server, project, ask=ask), "make notes")
    assert not asked  # Bash was redirected, Write is allowed in acceptEdits
    results = [e for e in events if isinstance(e, ev.ToolResult)]
    assert results[0].is_error and "use the Write tool" in results[0].content
    assert results[0].summary == "use the file tool instead"
    assert (project / "notes.md").read_text() == "# Notes\n"
    assert isinstance(events[-1], ev.Result) and events[-1].subtype == "success"


async def test_repeating_the_command_lets_it_through(mock_server: Any, project: Path) -> None:
    """A genuine need for the shell is never blocked: the same command again
    goes to the normal permission flow."""
    server = mock_server(
        [
            {"tool_calls": [{"name": "Bash", "arguments": {"command": "cat README"}}]},
            {"tool_calls": [{"name": "Bash", "arguments": {"command": "cat README"}}]},
            {"content": "ok"},
        ]
    )
    (project / "README").write_text("hello\n")
    asked: list[PermissionRequest] = []

    async def ask(req: PermissionRequest) -> PermissionAnswer:
        asked.append(req)
        return PermissionAnswer(allow=True)

    events = await run(make_agent(server, project, ask=ask), "show the readme")
    results = [e for e in events if isinstance(e, ev.ToolResult)]
    assert "use the Read tool" in results[0].content
    assert len(asked) == 1 and results[1].content.strip() == "hello"


async def test_steering_can_be_turned_off(mock_server: Any, project: Path) -> None:
    server = mock_server(
        [
            {"tool_calls": [{"name": "Bash", "arguments": {"command": HEREDOC}}]},
            {"content": "ok"},
        ]
    )
    asked: list[PermissionRequest] = []

    async def ask(req: PermissionRequest) -> PermissionAnswer:
        asked.append(req)
        return PermissionAnswer(allow=False)

    await run(make_agent(server, project, ask=ask, steer=False), "make notes")
    assert len(asked) == 1  # old behaviour: the user is asked about the heredoc


def test_prompt_and_tool_descriptions_point_to_file_tools(project: Path) -> None:
    from cmcoder.core.prompt import build_system_prompt
    from cmcoder.tools.bash import BashTool

    for tier in ("full", "compact"):
        prompt = build_system_prompt(project, project, tier=tier)
        assert "never" in prompt and "cat" in prompt and "Write" in prompt
    assert "Do NOT use it for file work" in BashTool.description
