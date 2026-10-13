"""Skills (Phase 3 item 5): SKILL.md folders, loaded on demand."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from cmcoder.cli.factory import AgentOptions, build_agent
from cmcoder.config.settings import config_dir, load_settings
from cmcoder.core.skills import SkillInput, SkillTool, load_skills, skills_prompt
from cmcoder.tools.base import ToolContext

from .conftest import API_KEY


def skill(folder: Path, name: str, description: str, body: str = "Do it.", **files: str) -> None:
    (folder / name).mkdir(parents=True, exist_ok=True)
    (folder / name / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}"
    )
    for rel, text in files.items():
        path = folder / name / rel.replace("__", "/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def test_loading_and_precedence(project: Path) -> None:
    proj = project / ".cmcoder" / "skills"
    skill(proj, "release", "Project release notes")
    skill(proj, "lint", "Lint the code")
    skill(config_dir() / "skills", "release", "My release notes")
    (proj / "nodesc").mkdir()
    (proj / "nodesc" / "SKILL.md").write_text("No frontmatter: no description, not listed.")
    found = load_skills(project)
    assert set(found) == {"release", "lint"}
    assert found["release"].description == "My release notes" and found["release"].origin == "user"
    section = skills_prompt(found)
    assert section is not None and "- lint: Lint the code" in section
    assert skills_prompt({}) is None


async def test_the_tool_loads_instructions_and_files(project: Path) -> None:
    proj = project / ".cmcoder" / "skills"
    skill(
        proj,
        "release",
        "Release notes",
        "Use template.md.",
        **{
            "template.md": "# Release {version}",
            "scripts__collect.sh": "git log",
            ".env": "TOKEN=x",
        },
    )
    (project / "secret.txt").write_text("outside")
    tool = SkillTool(load_skills(project))
    ctx = ToolContext(cwd=project, project_root=project)

    res = await tool.run(SkillInput(skill="release"), ctx)
    assert res.content.startswith("Use template.md.") and "---" not in res.content
    assert "scripts/collect.sh" in res.content and "template.md" in res.content
    res = await tool.run(SkillInput(skill="release", file="template.md"), ctx)
    assert res.content == "# Release {version}"
    res = await tool.run(SkillInput(skill="release", file="../../../secret.txt"), ctx)
    assert res.is_error and "outside" in res.content
    res = await tool.run(SkillInput(skill="release", file=".env"), ctx)
    assert res.is_error and "secrets" in res.content
    res = await tool.run(SkillInput(skill="nope"), ctx)
    assert res.is_error and "Skills: release" in res.content
    assert tool.read_only


async def test_skills_reach_the_model(
    mock_server: Any, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill(project / ".cmcoder" / "skills", "release", "Writes release notes")
    server = mock_server([{"content": "ok"}])
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CMCODER_BASE_URL", server.base_url)
    monkeypatch.setenv("CMCODER_API_KEY", API_KEY)
    settings = load_settings(project)
    agent = await build_agent(settings, AgentOptions(cwd=project, model="qwen3-27b"))
    try:
        events = [e async for e in agent.run("hi")]
        assert events[-1].result == "ok", events[-1]
    finally:
        await agent.close()
    request = server.requests[0]
    assert "- release: Writes release notes" in request["messages"][0]["content"]
    assert "Skill" in {t["function"]["name"] for t in request["tools"]}
