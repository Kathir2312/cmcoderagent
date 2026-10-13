"""Files a repository gives cmcoder (memory, commands, agents, skills) must
really be inside it: a symlink to a file elsewhere, or to a secrets file,
would send that file to the model (Phase 3 security review, finding 1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cmcoder.core.commands import load_commands
from cmcoder.core.prompt import load_memory_files
from cmcoder.core.skills import SkillInput, SkillTool, load_skills
from cmcoder.core.subagents import load_agents
from cmcoder.sensitive import safe_project_file
from cmcoder.tools.base import ToolContext

SECRET = "AKIA-not-for-the-model"


def link(path: Path, target: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.symlink_to(target, target_is_directory=target.is_dir())
    except OSError:  # Windows without the symlink privilege
        pytest.skip("symlinks aren't available here")


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    """A folder outside the project, standing in for ~/.aws or ~/.ssh."""
    folder = tmp_path / "home"
    folder.mkdir()
    (folder / "credentials").write_text(f"---\ndescription: x\n---\n{SECRET}\n")
    (folder / "skill").mkdir()
    (folder / "skill" / "SKILL.md").write_text("---\ndescription: Looks harmless\n---\nHi")
    (folder / "skill" / "credentials").write_text(SECRET)
    return folder


def test_memory_files_outside_the_project_are_ignored(project: Path, outside: Path) -> None:
    link(project / "CMCODER.md", outside / "credentials")
    (project / ".env").write_text(SECRET)
    sub = project / "sub"
    link(sub / "CMCODER.md", project / ".env")
    (project / "CMCODER.local.md").write_text("Use tabs.")
    texts = [m.content for m in load_memory_files(sub, project)]
    assert texts == ["Use tabs."]


def test_commands_agents_and_skills_outside_the_project_are_ignored(
    project: Path, outside: Path
) -> None:
    c = project / ".cmcoder"
    link(c / "commands" / "review.md", outside / "credentials")
    link(c / "agents" / "helper.md", outside / "credentials")
    link(c / "skills" / "release", outside / "skill")
    (c / "commands" / "ok.md").write_text("Fine.")
    assert set(load_commands(project)) == {"ok"}
    assert "helper" not in load_agents(project, project_trusted=True)
    assert load_skills(project) == {}


async def test_a_skill_file_cannot_point_outside(project: Path, outside: Path) -> None:
    folder = project / ".cmcoder" / "skills" / "release"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("---\ndescription: Release notes\n---\nGo.")
    link(folder / "notes.md", outside / "credentials")
    res = await SkillTool(load_skills(project)).run(
        SkillInput(skill="release", file="notes.md"), ToolContext(cwd=project, project_root=project)
    )
    assert res.is_error and SECRET not in res.content


def test_safe_project_file(project: Path, outside: Path) -> None:
    (project / "a.md").write_text("x")
    assert safe_project_file(project / "a.md", project)
    assert not safe_project_file(project / ".env", project)
    assert not safe_project_file(outside / "credentials", project)
