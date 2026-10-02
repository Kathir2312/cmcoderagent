"""The default tool set."""

from __future__ import annotations

from .base import Tool
from .bash import BashTool
from .files import EditTool, ReadTool, WriteTool
from .search import GlobTool, GrepTool
from .todo import TodoTool


def default_tools() -> list[Tool]:
    return [ReadTool(), WriteTool(), EditTool(), GlobTool(), GrepTool(), BashTool(), TodoTool()]
