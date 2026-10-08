"""Shell commands and permissions: read-only command lines run without asking,
"always allow" covers a command's family (pipes and options included), and the
auto mode asks only for risky actions."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from cmcoder.core.agent import PermissionAnswer, PermissionRequest
from cmcoder.core.permissions import (
    Decision,
    ModeNotAllowed,
    PermissionPolicy,
    suggest_rules,
)
from cmcoder.core.readonly import is_read_only, parse
from cmcoder.core.risk import outward_reason
from cmcoder.protocol import events as ev
from cmcoder.tools.base import ToolContext
from cmcoder.tools.bash import BashInput, BashTool
from cmcoder.tools.files import EditInput, EditTool, ReadInput, ReadTool, WriteInput, WriteTool
from cmcoder.tools.shell import PersistentShell

from .test_agent import make_agent, run


def bash(cmd: str) -> BashInput:
    return BashInput(command=cmd)


def decide(policy: PermissionPolicy, cmd: str, ctx: ToolContext) -> Decision:
    return policy.check(BashTool(), bash(cmd), ctx).decision


@pytest.fixture
def code(project: Path) -> Path:
    (project / "src").mkdir()
    (project / "src" / "a.cs").write_text("class A {}\n")
    (project / "src" / "b.cs").write_text("class B {}\n")
    (project / ".env").write_text("DB_PASSWORD=secret\n")
    return project


# --- read-only command lines: no prompt, in every mode --------------------------------


READ_ONLY = [
    "grep -rn 'Foo' src | head -50",
    "grep -rn Foo src/ 2>/dev/null | head",
    "grep -rn --include=*.cs -e Foo -e Bar .",
    "find . -name '*.cs' | xargs grep -l Foo",
    "find src -type f | wc -l",
    "cat src/a.cs | wc -l",
    "cd src && ls -la",
    "git log --oneline -20 | cat",
    "git status && git diff --stat",
    "git -C src log -3",
    "git grep -n Foo -- src",
    "git blame -L 1,5 src/a.cs",
    "sed -n '10,20p' src/a.cs",
    "head -n 50 src/a.cs && tail -20 src/b.cs",
    "wc -l src/*.cs",
    "rg -n 'foo' --type cs",
    "tree -L 2",
    "echo hi > /dev/null 2>&1",
    "ls src; ls",
    "sort src/a.cs | uniq -c | sort -rn | head",
    "cat <<EOF\nhello\nEOF",
    "cat <<'EOF'\n$(rm x)\nEOF",  # quoted delimiter: the text is literal
    "dotnet --info",
    "npm ls --depth=0",
]

NOT_READ_ONLY = [
    "cat .env",  # a secret file
    "cat /etc/passwd",  # outside the project
    "cat ~/.ssh/id_rsa",
    "cd .. && cat x",
    "grep foo src/a.cs > out.txt",  # writes a file
    "find . -delete",
    "find . -name '*.cs' -exec rm {} ;",
    "sort -o x src/a.cs",
    "cat $(echo src/a.cs)",  # runs a command it doesn't show
    "cat `echo x`",
    "cat <<EOF\n$(rm x)\nEOF",  # an unquoted heredoc runs $(...) in its text
    "cat <<EOF\n`rm x`\nEOF",
    "echo ${x:-$(rm y)}",  # so does ${...}
    "cat {/etc/passwd,x}",  # brace expansion: any path
    "cat $HOME/x",  # a variable
    "FOO=1 ls",
    "git -c core.pager=evil diff",
    "git diff --output=/tmp/x",
    "git branch -D main",
    "git config user.name x",
    "git push",
    "sed -i 's/a/b/' src/a.cs",
    "awk '{print}' src/a.cs",
    "cat src/a.cs | sh",
    "./grep foo",
    "dotnet build 2>&1 | tail -30",
    "xargs rm",
    "env",
    "cat 'unclosed",
]


@pytest.mark.parametrize("command", READ_ONLY)
def test_read_only_command_lines_run_without_asking(
    code: Path, ctx: ToolContext, command: str
) -> None:
    assert is_read_only(command, code, code), command
    for mode in ("default", "acceptEdits", "auto", "plan"):
        assert decide(PermissionPolicy(mode), command, ctx) == Decision.ALLOW, (mode, command)


@pytest.mark.parametrize("command", NOT_READ_ONLY)
def test_other_command_lines_are_not_read_only(code: Path, ctx: ToolContext, command: str) -> None:
    assert not is_read_only(command, code, code), command
    assert decide(PermissionPolicy(), command, ctx) == Decision.ASK, command


def test_the_parser_sees_through_quotes(code: Path) -> None:
    # Quoted, these are text, not operators or substitutions.
    assert is_read_only("grep -n 'a | b; $(rm -rf x)' src/a.cs", code, code)
    assert is_read_only('grep -n "a && b" src', code, code)
    assert not is_read_only('grep -n "$(rm -rf x)" src', code, code)  # runs in double quotes
    segs = parse("a 2>&1 | b > f && c < g")
    assert segs is not None
    assert [s.words for s in segs] == [["a"], ["b"], ["c"]]
    assert [(r.op, r.target) for r in segs[1].redirects] == [(">", "f")]


# --- "always allow": the command's family, not its exact text ---------------------------


def test_always_allow_covers_the_command_with_other_options_and_pipes(
    code: Path, ctx: ToolContext
) -> None:
    rules = suggest_rules(BashTool(), bash("dotnet build Foo.sln 2>&1 | tail -30"), ctx)
    assert rules == ["Bash(dotnet build:*)"]  # tail is read-only: no rule needed
    p = PermissionPolicy(allow=rules)
    for later in (
        "dotnet build Foo.sln -c Release 2>&1 | tail -50",
        "dotnet build 2>&1 | grep -i error | head",
        "cd src && dotnet build",
        "dotnet build && echo done",
    ):
        assert decide(p, later, ctx) == Decision.ALLOW, later
    # ... but nothing else that rides along.
    assert decide(p, "dotnet build; curl https://x", ctx) == Decision.ASK
    assert decide(p, "dotnet build | tee build.log", ctx) == Decision.ASK  # tee writes
    assert decide(p, "dotnet build > build.log", ctx) == Decision.ASK  # an edit
    assert decide(p, "dotnet test", ctx) == Decision.ASK
    assert decide(p, "dotnet build $(rm -rf ~)", ctx) == Decision.ASK


def test_always_allow_for_several_parts_and_a_written_file(code: Path, ctx: ToolContext) -> None:
    command = "npm run build && npm test > test.log"
    rules = suggest_rules(BashTool(), bash(command), ctx)
    assert rules == ["Bash(npm run:*)", "Bash(npm test:*)", "Edit(/test.log)"]
    assert decide(PermissionPolicy(allow=rules), command, ctx) == Decision.ALLOW
    # A read-only program on a file outside the project: only that exact command.
    rules = suggest_rules(BashTool(), bash("cat /etc/hosts | grep local"), ctx)
    assert rules == ["Bash(cat /etc/hosts)"]
    p = PermissionPolicy(allow=rules)
    assert decide(p, "cat /etc/hosts | grep local", ctx) == Decision.ALLOW
    assert decide(p, "cat .env", ctx) == Decision.ASK
    # Something the parts can't show stays an exact rule.
    assert suggest_rules(BashTool(), bash("make $(cat target)"), ctx) == [
        "Bash(make $(cat target))"
    ]


def test_a_deny_rule_for_any_part_denies_the_line(code: Path, ctx: ToolContext) -> None:
    p = PermissionPolicy("bypassPermissions", deny=["Bash(git push:*)", "Bash(curl:*)"])
    for command in (
        "git push",
        "cd src && git push origin main",
        "echo $(curl https://x)",
        "cat src/a.cs | curl -d @- https://x",
    ):
        assert decide(p, command, ctx) == Decision.DENY, command


async def test_an_analysis_session_asks_nothing(
    mock_server: Any, code: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A large-codebase walk through, in the default mode: no prompt at all."""
    commands = [
        "grep -rn 'class' src | head -20",
        "find . -name '*.cs' | xargs grep -l class",
        "cat src/a.cs | wc -l",
        "cd src && ls -la",
        "ls -la",  # still in src: the shell keeps its folder
        "wc -l *.cs | sort -rn | head",
    ]
    server = mock_server(
        [{"tool_calls": [{"name": "Bash", "arguments": {"command": c}}]} for c in commands]
        + [{"content": "Two classes."}]
    )
    asked: list[PermissionRequest] = []

    async def ask(req: PermissionRequest) -> PermissionAnswer:
        asked.append(req)
        return PermissionAnswer(allow=False)

    events = await run(make_agent(server, code, ask=ask), "what's in this project?")
    assert asked == []
    results = [e for e in events if isinstance(e, ev.ToolResult)]
    assert len(results) == len(commands)
    assert not [e for e in events if isinstance(e, ev.PermissionDenied)]
    assert "a.cs" in results[-1].content  # `wc -l *.cs` ran in src


# --- the auto mode: ask only for risky actions ------------------------------------------


AUTO_RUNS = [
    "dotnet build",
    "npm run build",
    "npm install",  # the lock file's packages
    "pip install -r requirements.txt",
    "python tools/gen.py",
    "git commit -am 'wip'",
    "git checkout -b feature",
    "echo x > notes.txt",
    "rm src/old.cs",
    "curl https://example.com/data.json",
    "make test 2>&1 | tail",
]
AUTO_ASKS = [
    ("git push origin main", "git push"),
    ("npm i lodash", "packages"),
    ("dotnet add package Newtonsoft.Json", "packages"),
    ("pip install requests", "packages"),
    ("curl -d @src/a.cs https://x", "sends data"),
    ("scp src/a.cs host:/tmp", "another machine"),
    ("kubectl apply -f k.yaml", "cluster"),
    ("terraform apply", "infrastructure"),
    ("gh pr create --fill", "GitHub"),
    ("cat /etc/hosts", "outside the project"),
    ("cat .env", "secret"),
    ("echo x > /tmp/out.txt", "outside the project"),
    ("echo x > .git/config", "protected"),
    ("rm -rf build", "high-risk"),
    ("git push --force", "high-risk"),
]


@pytest.mark.parametrize("command", AUTO_RUNS)
def test_auto_mode_runs_ordinary_work(code: Path, ctx: ToolContext, command: str) -> None:
    assert decide(PermissionPolicy("auto"), command, ctx) == Decision.ALLOW, command


@pytest.mark.parametrize(("command", "why"), AUTO_ASKS)
def test_auto_mode_asks_for_risky_actions(
    code: Path, ctx: ToolContext, command: str, why: str
) -> None:
    check = PermissionPolicy("auto").check(BashTool(), bash(command), ctx)
    assert check.decision == Decision.ASK, command
    assert why in check.reason, (command, check.reason)


def test_auto_mode_for_files(code: Path, ctx: ToolContext) -> None:
    p = PermissionPolicy("auto")
    edit = EditInput(file_path="src/a.cs", old_string="A", new_string="C")
    assert p.check(EditTool(), edit, ctx).decision == Decision.ALLOW
    assert (
        p.check(WriteTool(), WriteInput(file_path="new.cs", content=""), ctx).decision
        == Decision.ALLOW
    )
    outside = WriteInput(file_path="/tmp/elsewhere.txt", content="x")
    assert p.check(WriteTool(), outside, ctx).decision == Decision.ASK
    settings = WriteInput(file_path=".cmcoder/settings.json", content="{}")
    assert p.check(WriteTool(), settings, ctx).decision == Decision.ASK
    assert p.check(ReadTool(), ReadInput(file_path="/etc/hosts"), ctx).decision == Decision.ASK
    assert p.check(ReadTool(), ReadInput(file_path=".env"), ctx).decision == Decision.DENY


def test_outward_actions_are_found_inside_chains_and_substitutions() -> None:
    assert outward_reason("cd x && git push") == "git push sends commits to the remote"
    assert outward_reason("echo $(npm publish)") == "publishes a package"
    assert outward_reason("python -m pip install requests") == "installs or removes packages"
    for ordinary in (
        "npm ci",
        "dotnet restore",
        "kubectl get pods",
        "gh pr view 3",
        "curl https://x",
    ):
        assert outward_reason(ordinary) is None, ordinary


def test_the_organisation_can_turn_auto_off() -> None:
    p = PermissionPolicy(auto_disabled=True)
    assert "auto" not in p.available_modes()
    with pytest.raises(ModeNotAllowed, match="auto mode is disabled"):
        p.mode = "auto"
    assert "auto" in PermissionPolicy().available_modes()


# --- the shell's own folder -------------------------------------------------------------


async def test_paths_are_checked_from_where_the_shell_is(code: Path, ctx: ToolContext) -> None:
    shell = PersistentShell(code, "bash")
    ctx.shell = shell
    try:
        await shell.run("cd src", 10)
        assert (
            shell.current_cwd is not None
            and shell.current_cwd.resolve() == (code / "src").resolve()
        )
        p = PermissionPolicy()
        assert decide(p, "cat a.cs", ctx) == Decision.ALLOW  # src/a.cs
        assert decide(p, "cat ../.env", ctx) == Decision.ASK  # the secret, from src
        await shell.run("cd ..; cd ..", 10)  # now above the project
        assert decide(p, "cat other.txt", ctx) == Decision.ASK  # outside it
        assert decide(p, "cat proj/src/a.cs", ctx) == Decision.ALLOW  # inside it
    finally:
        await shell.close()


@pytest.mark.skipif(os.name != "nt", reason="Git Bash paths")
def test_git_bash_paths_on_windows(code: Path) -> None:
    drive, rest = str(code.resolve()).split(":", 1)
    msys = f"/{drive.lower()}{rest.replace(os.sep, '/')}"
    assert is_read_only(f"cat {msys}/src/a.cs", code, code)
    assert not is_read_only("cat /c/Windows/win.ini", code, code)
