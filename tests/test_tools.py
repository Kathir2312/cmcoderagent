from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

from cmcoder.tools.base import ToolContext
from cmcoder.tools.bash import BashInput, BashTool
from cmcoder.tools.files import EditInput, EditTool, ReadInput, ReadTool, WriteInput, WriteTool
from cmcoder.tools.search import GlobInput, GlobTool, GrepInput, GrepTool


async def read(ctx: ToolContext, path: str, **kw: int) -> str:
    res = await ReadTool().run(ReadInput(file_path=path, **kw), ctx)
    assert not res.is_error, res.content
    return res.content


async def test_read_numbers_lines_and_pages(ctx: ToolContext, project: Path) -> None:
    (project / "a.txt").write_text("one\ntwo\nthree\n")
    assert await read(ctx, "a.txt") == "     1\tone\n     2\ttwo\n     3\tthree"
    out = await read(ctx, "a.txt", offset=2, limit=1)
    assert out.startswith("     2\ttwo")
    assert "showing lines 2-2 of 3" in out


async def test_read_errors(ctx: ToolContext, project: Path) -> None:
    (project / "bin").write_bytes(b"\x00\x01\x02")
    assert (await ReadTool().run(ReadInput(file_path="missing.txt"), ctx)).is_error
    assert "binary" in (await ReadTool().run(ReadInput(file_path="bin"), ctx)).content
    assert "directory" in (await ReadTool().run(ReadInput(file_path="."), ctx)).content


async def test_write_requires_read_for_existing_files(ctx: ToolContext, project: Path) -> None:
    (project / "a.txt").write_text("old")
    res = await WriteTool().run(WriteInput(file_path="a.txt", content="new"), ctx)
    assert res.is_error and "Read it first" in res.content
    await read(ctx, "a.txt")
    res = await WriteTool().run(WriteInput(file_path="a.txt", content="new"), ctx)
    assert not res.is_error
    assert (project / "a.txt").read_text() == "new"


async def test_write_creates_new_files_and_dirs(ctx: ToolContext, project: Path) -> None:
    res = await WriteTool().run(WriteInput(file_path="src/pkg/new.py", content="x = 1\n"), ctx)
    assert not res.is_error
    assert (project / "src/pkg/new.py").read_text() == "x = 1\n"


async def test_write_detects_external_change(ctx: ToolContext, project: Path) -> None:
    f = project / "a.txt"
    f.write_text("v1")
    await read(ctx, "a.txt")
    import os

    os.utime(f, ns=(f.stat().st_atime_ns, f.stat().st_mtime_ns + 10_000_000))
    res = await WriteTool().run(WriteInput(file_path="a.txt", content="v2"), ctx)
    assert res.is_error and "modified since" in res.content


async def test_edit_replaces_unique_string(ctx: ToolContext, project: Path) -> None:
    (project / "m.py").write_text("def f():\n    return 1\n")
    await read(ctx, "m.py")
    res = await EditTool().run(
        EditInput(file_path="m.py", old_string="return 1", new_string="return 2"), ctx
    )
    assert not res.is_error, res.content
    assert (project / "m.py").read_text() == "def f():\n    return 2\n"
    assert "return 2" in res.content


async def test_edit_errors(ctx: ToolContext, project: Path) -> None:
    (project / "m.py").write_text("x = 1\nx = 1\n")
    edit = EditTool()
    res = await edit.run(EditInput(file_path="m.py", old_string="x = 1", new_string="x = 2"), ctx)
    assert "has not been read" in res.content
    await read(ctx, "m.py")
    res = await edit.run(EditInput(file_path="m.py", old_string="x = 1", new_string="x = 2"), ctx)
    assert res.is_error and "occurs 2 times" in res.content
    res = await edit.run(EditInput(file_path="m.py", old_string="y = 1", new_string="y = 2"), ctx)
    assert res.is_error and "not found" in res.content
    res = await edit.run(
        EditInput(file_path="m.py", old_string="x = 1", new_string="x = 2", replace_all=True), ctx
    )
    assert not res.is_error
    assert (project / "m.py").read_text() == "x = 2\nx = 2\n"


async def test_edit_preserves_crlf(ctx: ToolContext, project: Path) -> None:
    (project / "w.txt").write_bytes(b"a\r\nb\r\n")
    await read(ctx, "w.txt")
    res = await EditTool().run(
        EditInput(file_path="w.txt", old_string="a\nb", new_string="a\nc"), ctx
    )
    assert not res.is_error, res.content
    assert (project / "w.txt").read_bytes() == b"a\r\nc\r\n"


@pytest.fixture(params=["ripgrep", "python"])
def search_engine(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    """Run search tests with ripgrep (when installed) and with the built-in fallback."""
    if request.param == "ripgrep":
        if shutil.which("rg") is None:
            pytest.skip("ripgrep not installed")
    else:
        monkeypatch.setattr("cmcoder.tools.search.ripgrep", lambda: None)
    return str(request.param)


async def test_glob_respects_gitignore(ctx: ToolContext, project: Path, search_engine: str) -> None:
    (project / ".gitignore").write_text("build/\n")
    (project / "src").mkdir()
    (project / "src/a.py").write_text("")
    (project / "build").mkdir()
    (project / "build/b.py").write_text("")
    (project / ".hidden").mkdir()
    (project / ".hidden/c.py").write_text("")
    res = await GlobTool().run(GlobInput(pattern="**/*.py"), ctx)
    assert os.path.join("src", "a.py") in res.content
    assert "build" not in res.content and ".hidden" not in res.content
    res = await GlobTool().run(GlobInput(pattern="*.py"), ctx)  # any depth, like ripgrep
    assert os.path.join("src", "a.py") in res.content


async def test_grep_modes(ctx: ToolContext, project: Path, search_engine: str) -> None:
    (project / "a.py").write_text("def hello():\n    pass\n")
    (project / "b.py").write_text("HELLO = 1\n")
    (project / "notes.md").write_text("hello in markdown\n")
    (project / "sub").mkdir()
    (project / "sub/c.py").write_text("x = 1\ny = 2\nhello()\nz = 3\n")
    (project / ".gitignore").write_text("ignored.py\n")
    (project / "ignored.py").write_text("hello\n")
    grep = GrepTool()
    sub_c = os.path.join("sub", "c.py")

    files = await grep.run(GrepInput(pattern="hello", type="py"), ctx)
    assert sorted(files.content.splitlines()) == ["a.py", sub_c]

    content = await grep.run(
        GrepInput(pattern="hello", output_mode="content", case_insensitive=True, glob="*.py"), ctx
    )
    lines = content.content.splitlines()
    assert "a.py:1:def hello():" in lines
    assert "b.py:1:HELLO = 1" in lines
    assert not any("notes" in line for line in lines), lines
    # Like ripgrep, an explicit glob re-includes git-ignored files...
    assert "ignored.py:1:hello" in lines
    # ...but without a glob they stay excluded, and ignored directories are always skipped.
    plain = await grep.run(GrepInput(pattern="hello"), ctx)
    assert "ignored.py" not in plain.content
    (project / "build").mkdir()
    (project / "build/out.py").write_text("hello\n")
    (project / ".gitignore").write_text("ignored.py\nbuild/\n")
    globbed = await grep.run(GrepInput(pattern="hello", glob="*.py"), ctx)
    assert "build" not in globbed.content

    with_ctx = await grep.run(
        GrepInput(pattern="hello", path="sub", output_mode="content", context_lines=1), ctx
    )
    assert with_ctx.content.splitlines() == [
        f"{sub_c}-2-y = 2",
        f"{sub_c}:3:hello()",
        f"{sub_c}-4-z = 3",
    ]

    count = await grep.run(GrepInput(pattern="=", path="sub", output_mode="count"), ctx)
    assert count.content.strip() == f"{sub_c}:3"

    multi = await grep.run(GrepInput(pattern=r"def hello\(\):\s+pass", multiline=True), ctx)
    assert multi.content.strip() == "a.py"

    none = await grep.run(GrepInput(pattern="nomatch"), ctx)
    assert none.content == "No matches found."


async def test_grep_bad_regex_is_reported(
    ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("cmcoder.tools.search.ripgrep", lambda: None)
    res = await GrepTool().run(GrepInput(pattern="(unclosed"), ctx)
    assert res.is_error and "Invalid regular expression" in res.content


async def test_bash_persists_state_and_reports_exit_codes(ctx: ToolContext, project: Path) -> None:
    bash = BashTool()
    try:
        (project / "sub").mkdir()
        await bash.run(BashInput(command="cd sub && export FOO=bar"), ctx)
        res = await bash.run(BashInput(command='pwd; echo "$FOO"'), ctx)
        cwd, foo = res.content.splitlines()
        assert cwd.endswith("/sub") and foo == "bar"
        res = await bash.run(
            BashInput(command="echo oops >&2; exit_code() { return 3; }; exit_code"), ctx
        )
        assert res.is_error and "oops" in res.content and "Exit code 3" in res.content
        # Syntax errors fail the command but keep the shell alive.
        res = await bash.run(BashInput(command="if then fi"), ctx)
        assert res.is_error
        res = await bash.run(BashInput(command="echo $FOO"), ctx)
        assert res.content == "bar"
        # Commands get no stdin, so prompts fail instead of hanging.
        res = await bash.run(BashInput(command="read x; echo got:$x"), ctx)
        assert res.content.startswith("got:")
    finally:
        assert ctx.shell
        await ctx.shell.close()


async def test_bash_timeout_and_exit_restart(ctx: ToolContext) -> None:
    bash = BashTool()
    try:
        res = await bash.run(BashInput(command="sleep 5", timeout_seconds=1), ctx)
        assert res.is_error and "timed out" in res.content
        res = await bash.run(BashInput(command="echo alive"), ctx)
        assert res.content == "alive"
        res = await bash.run(BashInput(command="exit 4"), ctx)
        assert "shell exited" in res.content
        res = await bash.run(BashInput(command="echo back"), ctx)
        assert res.content == "back"
    finally:
        assert ctx.shell
        await ctx.shell.close()


async def test_bash_missing_shell_is_explained(
    ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("cmcoder.tools.bash.find_shell", lambda: None)
    res = await BashTool().run(BashInput(command="echo hi"), ctx)
    assert res.is_error and "Git for Windows" in res.content


async def test_write_keeps_line_endings_exactly(ctx: ToolContext, project: Path) -> None:
    await WriteTool().run(WriteInput(file_path="lf.txt", content="a\nb\n"), ctx)
    assert (project / "lf.txt").read_bytes() == b"a\nb\n"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink semantics")
async def test_context_resolves_symlinked_roots(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    c = ToolContext(cwd=link, project_root=link)
    assert c.cwd == real and c.resolve("a.txt") == real / "a.txt"


async def test_grep_never_prints_secret_files(
    ctx: ToolContext, project: Path, search_engine: str
) -> None:
    (project / "secrets").mkdir()
    (project / "secrets/db.txt").write_text("password=hunter2\n")
    (project / "server.key").write_text("hunter2 PRIVATE KEY\n")
    (project / "src").mkdir()
    (project / "src/deploy.pem").write_text("hunter2\n")
    (project / "src/app.py").write_text("print('hunter2 is not a secret here')\n")
    grep = GrepTool()
    res = await grep.run(GrepInput(pattern="hunter2", output_mode="content"), ctx)
    assert res.content.splitlines() == [
        f"{os.path.join('src', 'app.py')}:1:print('hunter2 is not a secret here')"
    ]
    # Also when searching a subfolder, and with an explicit glob.
    sub = await grep.run(GrepInput(pattern="hunter2", path="src", glob="*"), ctx)
    assert sub.content.strip() == os.path.join("src", "app.py")


async def test_read_pages_long_files_at_line_boundaries(project: Path) -> None:
    ctx = ToolContext(cwd=project, project_root=project, max_output_chars=2_000)
    (project / "long.py").write_text("".join(f"line_{i} = {i}\n" for i in range(1, 1001)))
    first = await ReadTool().run(ReadInput(file_path="long.py"), ctx)
    assert len(first.content) <= 2_000
    body, _, footer = first.content.rpartition("\n\n")
    last_shown = int(body.splitlines()[-1].split("\t")[0])
    assert f"offset={last_shown + 1}" in footer and "of 1000" in footer
    assert "truncated" not in first.content  # no silent middle cut
    nxt = await ReadTool().run(ReadInput(file_path="long.py", offset=last_shown + 1), ctx)
    assert nxt.content.startswith(f"{last_shown + 1:>6}\tline_{last_shown + 1} =")


async def test_read_refuses_huge_files(
    ctx: ToolContext, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("cmcoder.tools.files.MAX_READ_BYTES", 100)
    (project / "big.log").write_text("x" * 200)
    res = await ReadTool().run(ReadInput(file_path="big.log"), ctx)
    assert res.is_error and "Grep" in res.content


def test_output_budget_scales_with_context() -> None:
    from cmcoder.tools.base import output_budget_chars

    assert output_budget_chars(32_768) < output_budget_chars(131_072) == 30_000
    assert output_budget_chars(4_096) >= 4_000
