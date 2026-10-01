from __future__ import annotations

import shutil
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


needs_rg = pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep not installed")


@needs_rg
async def test_glob_respects_gitignore(ctx: ToolContext, project: Path) -> None:
    (project / ".gitignore").write_text("build/\n")
    (project / "src").mkdir()
    (project / "src/a.py").write_text("")
    (project / "build").mkdir()
    (project / "build/b.py").write_text("")
    res = await GlobTool().run(GlobInput(pattern="**/*.py"), ctx)
    assert "src/a.py" in res.content
    assert "build" not in res.content


@needs_rg
async def test_grep_modes(ctx: ToolContext, project: Path) -> None:
    (project / "a.py").write_text("def hello():\n    pass\n")
    (project / "b.py").write_text("HELLO = 1\n")
    files = await GrepTool().run(GrepInput(pattern="hello"), ctx)
    assert files.content.strip() == "a.py"
    content = await GrepTool().run(
        GrepInput(pattern="hello", output_mode="content", case_insensitive=True), ctx
    )
    assert "a.py:1:def hello():" in content.content
    assert "b.py:1:HELLO = 1" in content.content
    none = await GrepTool().run(GrepInput(pattern="nomatch"), ctx)
    assert none.content == "No matches found."


async def test_bash_persists_state_and_reports_exit_codes(ctx: ToolContext, project: Path) -> None:
    bash = BashTool()
    try:
        (project / "sub").mkdir()
        await bash.run(BashInput(command="cd sub && export FOO=bar"), ctx)
        res = await bash.run(BashInput(command='pwd; echo "$FOO"'), ctx)
        assert res.content.splitlines() == [str(project / "sub"), "bar"]
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
