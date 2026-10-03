"""Skills: know-how in folders, loaded only when the model needs it.

    ~/.cmcoder/skills/release-notes/SKILL.md   (yours, every project)
    .cmcoder/skills/release-notes/SKILL.md     (this project's; yours win)

SKILL.md has a name and a description in frontmatter (Claude Code's format):

    ---
    name: release-notes
    description: Writes release notes in our format. Use when asked for release notes.
    ---
    1. Collect the merged changes with `git log` ...
    See template.md for the layout.

Only names and descriptions go into the system prompt. The `Skill` tool returns
the instructions when the model decides a skill is relevant, and the skill's
other files (templates, references, scripts) with `file`. A skill is text: it
never runs anything by itself and grants no permissions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import Field

from ..config.settings import config_dir
from ..sensitive import is_secret
from ..tools.base import Tool, ToolContext, ToolInput, ToolResult
from .commands import parse_file

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
MAX_SKILLS = 50  # listed in the prompt
MAX_DESCRIPTION_CHARS = 300
MAX_FILE_CHARS = 100_000
MAX_LISTED_FILES = 50


@dataclass
class Skill:
    name: str
    description: str
    folder: Path
    origin: Literal["user", "project"]


def _load_dir(folder: Path, origin: Literal["user", "project"]) -> dict[str, Skill]:
    found: dict[str, Skill] = {}
    if not folder.is_dir():
        return found
    for skill_md in sorted(folder.glob("*/SKILL.md")):
        try:
            meta, _ = parse_file(skill_md.read_text(encoding="utf-8")[:MAX_FILE_CHARS])
        except (OSError, UnicodeDecodeError):
            continue
        name = meta.get("name") or skill_md.parent.name
        description = " ".join(meta.get("description", "").split())
        if not NAME_RE.match(name) or not description:
            continue  # the description is how the model finds it: required
        found[name] = Skill(name, description[:MAX_DESCRIPTION_CHARS], skill_md.parent, origin)
    return found


def load_skills(project_root: Path) -> dict[str, Skill]:
    """This project's skills, then yours on top (yours win on a name clash)."""
    skills = _load_dir(project_root / ".cmcoder" / "skills", "project")
    skills.update(_load_dir(config_dir() / "skills", "user"))
    return skills


def skills_prompt(skills: dict[str, Skill]) -> str | None:
    """The system prompt section listing the skills (None: no skills)."""
    if not skills:
        return None
    lines = [f"- {s.name}: {s.description}" for s in list(skills.values())[:MAX_SKILLS]]
    return (
        "# Skills\nSkills are instructions for specific tasks. When a task matches a "
        "skill's description, load it with the Skill tool first and follow it.\n" + "\n".join(lines)
    )


class SkillInput(ToolInput):
    skill: str = Field(description="The skill's name")
    file: str | None = Field(
        None, description="Another file of the skill to read, relative to its folder"
    )


class SkillTool(Tool):
    name: ClassVar[str] = "Skill"
    description: ClassVar[str] = (
        "Load a skill (listed under Skills in the system prompt): its instructions, or one of "
        "its other files with `file`. Load a skill before doing a task that matches it."
    )
    Input = SkillInput
    read_only: ClassVar[bool] = True  # reads only the skill folders

    def __init__(self, skills: dict[str, Skill]) -> None:
        self.skills = skills

    def describe(self, args: SkillInput, ctx: ToolContext) -> str:
        return f"Skill({args.skill}{'/' + args.file if args.file else ''})"

    async def run(self, args: SkillInput, ctx: ToolContext) -> ToolResult:
        skill = self.skills.get(args.skill)
        if skill is None:
            names = ", ".join(self.skills) or "none"
            return ToolResult(f"No skill named {args.skill!r}. Skills: {names}.", is_error=True)
        folder = skill.folder.resolve()
        path = (folder / (args.file or "SKILL.md")).resolve()
        if not path.is_relative_to(folder):
            return ToolResult(f"{args.file} is outside the skill's folder.", is_error=True)
        if is_secret(path, ctx.project_root):  # the same protection as Read
            return ToolResult(f"{args.file} looks like a secrets file.", is_error=True)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[:MAX_FILE_CHARS]
        except OSError:
            return ToolResult(f"The skill {skill.name} has no file {args.file}.", is_error=True)
        if args.file:
            return ToolResult(text, summary=f"{args.file} of {skill.name}")
        _, body = parse_file(text)
        others = sorted(
            p.relative_to(folder).as_posix()
            for p in folder.rglob("*")
            if p.is_file() and p.name != "SKILL.md"
        )
        if others:
            listed = ", ".join(others[:MAX_LISTED_FILES])
            more = (
                f" (and {len(others) - MAX_LISTED_FILES} more)"
                if len(others) > MAX_LISTED_FILES
                else ""
            )
            body += (
                f"\n\n(The skill's other files: {listed}{more}. Read one with "
                f'Skill(skill="{skill.name}", file="...").)'
            )
        return ToolResult(body, summary=f"loaded {skill.name}")
