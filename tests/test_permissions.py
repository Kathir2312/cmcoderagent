from __future__ import annotations

from pathlib import Path

import pytest

from cmcoder.core.permissions import Decision, PermissionPolicy, command_matches, suggest_rule
from cmcoder.tools.base import ToolContext
from cmcoder.tools.bash import BashInput, BashTool
from cmcoder.tools.files import EditInput, EditTool, ReadInput, ReadTool, WriteInput, WriteTool


def check(policy: PermissionPolicy, tool, args, ctx: ToolContext) -> Decision:  # type: ignore[no-untyped-def]
    return policy.check(tool, args, ctx).decision


def bash(cmd: str) -> BashInput:
    return BashInput(command=cmd)


def test_command_prefix_rules_reject_chained_commands() -> None:
    assert command_matches("npm test:*", "npm test")
    assert command_matches("npm test:*", "npm test -- --watch=false")
    assert not command_matches("npm test:*", "npm tests")
    assert not command_matches("npm test:*", "npm test; rm -rf ~")
    assert not command_matches("npm test:*", "npm test && curl evil")
    assert not command_matches("npm test:*", "npm test $(whoami)")
    assert command_matches("git status", "git status")
    assert not command_matches("git status", "git status -s")


def test_default_mode(ctx: ToolContext, project: Path) -> None:
    p = PermissionPolicy()
    assert check(p, ReadTool(), ReadInput(file_path="src/a.py"), ctx) == Decision.ALLOW
    assert check(p, ReadTool(), ReadInput(file_path="/etc/hosts"), ctx) == Decision.ASK
    assert (
        check(p, EditTool(), EditInput(file_path="a.py", old_string="a", new_string="b"), ctx)
        == Decision.ASK
    )
    assert check(p, BashTool(), bash("git status"), ctx) == Decision.ALLOW
    assert check(p, BashTool(), bash("git status; rm -rf /"), ctx) == Decision.ASK
    assert check(p, BashTool(), bash("rm -rf build"), ctx) == Decision.ASK


def test_accept_edits_plan_and_bypass(ctx: ToolContext) -> None:
    edit = EditInput(file_path="a.py", old_string="a", new_string="b")
    outside = WriteInput(file_path="/tmp/elsewhere.txt", content="x")
    accept = PermissionPolicy("acceptEdits")
    assert check(accept, EditTool(), edit, ctx) == Decision.ALLOW
    assert check(accept, WriteTool(), outside, ctx) == Decision.ASK
    assert check(accept, BashTool(), bash("make"), ctx) == Decision.ASK
    plan = PermissionPolicy("plan")
    assert check(plan, EditTool(), edit, ctx) == Decision.DENY
    assert check(plan, BashTool(), bash("make"), ctx) == Decision.DENY
    assert check(plan, BashTool(), bash("git diff"), ctx) == Decision.ALLOW
    bypass = PermissionPolicy("bypassPermissions")
    assert check(bypass, BashTool(), bash("make"), ctx) == Decision.ALLOW
    assert check(bypass, WriteTool(), outside, ctx) == Decision.ALLOW


def test_rules_and_precedence(ctx: ToolContext) -> None:
    p = PermissionPolicy(
        allow=["Bash(npm test:*)", "Edit(src/**)"],
        deny=["Bash(npm test -- --update:*)", "Read(docs/private/**)"],
    )
    assert check(p, BashTool(), bash("npm test"), ctx) == Decision.ALLOW
    assert check(p, BashTool(), bash("npm test -- --update"), ctx) == Decision.DENY
    assert (
        check(p, EditTool(), EditInput(file_path="src/x.py", old_string="a", new_string="b"), ctx)
        == Decision.ALLOW
    )
    # Edit rules cover Write too.
    assert (
        check(p, WriteTool(), WriteInput(file_path="src/new.py", content=""), ctx) == Decision.ALLOW
    )
    assert (
        check(p, WriteTool(), WriteInput(file_path="lib/new.py", content=""), ctx) == Decision.ASK
    )
    assert check(p, ReadTool(), ReadInput(file_path="docs/private/a.md"), ctx) == Decision.DENY


@pytest.mark.parametrize(
    "name", [".env", ".env.production", "certs/server.pem", "secrets/db.txt", "id_rsa"]
)
def test_secret_files_are_blocked_even_in_bypass(ctx: ToolContext, name: str) -> None:
    p = PermissionPolicy("bypassPermissions")
    assert check(p, ReadTool(), ReadInput(file_path=name), ctx) == Decision.DENY


def test_explicit_allow_overrides_secret_protection(ctx: ToolContext) -> None:
    p = PermissionPolicy(allow=["Read(.env.example)"])
    assert check(p, ReadTool(), ReadInput(file_path=".env.example"), ctx) == Decision.ALLOW


def test_suggested_rules(ctx: ToolContext) -> None:
    assert suggest_rule(BashTool(), bash("npm test -- -x"), ctx) == "Bash(npm test:*)"
    assert suggest_rule(BashTool(), bash("pytest -q"), ctx) == "Bash(pytest:*)"
    assert suggest_rule(BashTool(), bash("a && b"), ctx) == "Bash(a && b)"
    assert (
        suggest_rule(
            EditTool(), EditInput(file_path="src/x.py", old_string="a", new_string="b"), ctx
        )
        == "Edit(src/**)"
    )
    assert (
        suggest_rule(WriteTool(), WriteInput(file_path="top.py", content=""), ctx)
        == "Edit(/top.py)"
    )


def test_suggested_rules_actually_match(ctx: ToolContext) -> None:
    for tool, args in [
        (BashTool(), bash("npm test -- -x")),
        (EditTool(), EditInput(file_path="src/x.py", old_string="a", new_string="b")),
        (WriteTool(), WriteInput(file_path="top.py", content="")),
    ]:
        p = PermissionPolicy(allow=[suggest_rule(tool, args, ctx)])
        assert check(p, tool, args, ctx) == Decision.ALLOW


def test_invalid_mode_rejected() -> None:
    with pytest.raises(ValueError):
        PermissionPolicy("yolo")


# --- Found in validation: auto-approved commands that write, protected paths, credentials ---


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("git branch", Decision.ALLOW),
        ("git branch -a -v", Decision.ALLOW),
        ("git branch -D main", Decision.ASK),
        ("git branch -m new-name", Decision.ASK),
        ("git diff HEAD~1", Decision.ALLOW),
        ("git diff --output=/tmp/x", Decision.ASK),
        ("git log --oneline -5", Decision.ALLOW),
        ("git -c core.pager=evil diff", Decision.ASK),
        ("date", Decision.ALLOW),
        ("date +%Y-%m-%d", Decision.ALLOW),
        ("date -s 2020-01-01", Decision.ASK),
        ("ls -la src", Decision.ALLOW),
        ("ls; rm -rf /", Decision.ASK),
    ],
)
def test_safe_commands_are_really_read_only(
    ctx: ToolContext, command: str, expected: Decision
) -> None:
    assert check(PermissionPolicy(), BashTool(), bash(command), ctx) == expected


@pytest.mark.parametrize(
    "path",
    [
        ".cmcoder/settings.json",
        ".cmcoder/settings.local.json",
        ".git/hooks/pre-commit",
        ".git/config",
    ],
)
def test_protected_paths_always_ask_before_edits(ctx: ToolContext, path: str) -> None:
    write = WriteInput(file_path=path, content="x")
    assert check(PermissionPolicy("acceptEdits"), WriteTool(), write, ctx) == Decision.ASK
    # Even a broad allow rule doesn't let the agent rewrite its own permissions.
    assert (
        check(PermissionPolicy("acceptEdits", allow=["Edit"]), WriteTool(), write, ctx)
        == Decision.ASK
    )
    assert check(PermissionPolicy("plan"), WriteTool(), write, ctx) == Decision.ASK
    # bypassPermissions means the user accepted the risk.
    assert check(PermissionPolicy("bypassPermissions"), WriteTool(), write, ctx) == Decision.ALLOW
    # Reading them is fine.
    assert check(PermissionPolicy(), ReadTool(), ReadInput(file_path=path), ctx) == Decision.ALLOW


def test_user_config_dir_is_protected_and_credentials_are_secret(ctx: ToolContext) -> None:
    from cmcoder.config.settings import config_dir

    creds = str(config_dir() / "credentials.json")
    settings = str(config_dir() / "settings.json")
    assert check(PermissionPolicy(), ReadTool(), ReadInput(file_path=creds), ctx) == Decision.DENY
    assert (
        check(PermissionPolicy("bypassPermissions"), ReadTool(), ReadInput(file_path=creds), ctx)
        == Decision.DENY
    )
    edit = WriteInput(file_path=settings, content="{}")
    assert check(PermissionPolicy("acceptEdits"), WriteTool(), edit, ctx) == Decision.ASK


def test_secrets_folder_itself_is_secret(ctx: ToolContext) -> None:
    from cmcoder.tools.search import GrepInput, GrepTool

    assert (
        check(PermissionPolicy(), GrepTool(), GrepInput(pattern="x", path="secrets"), ctx)
        == Decision.DENY
    )
