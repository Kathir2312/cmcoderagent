"""LangGraph Studio's entry point for the M3 graph (`langgraph dev` reads
`graph` from here; see langgraph.json and docs/learn/phase8/m3-graph-by-hand.md).

The model comes from your shell, as cmcoder's own setup takes it:
CMCODER_BASE_URL, CMCODER_API_KEY (read here, never printed or stored) and
CMCODER_MODEL. The tools are read-only (Read, Glob, Grep), since this graph
has no permission checks, and look at the folder CMCODER_LAB_PROJECT names
(else the folder `langgraph dev` runs in)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # the labs folder

from m3_graph import build_graph  # noqa: E402

from cmcoder.engine.tools import langchain_tools  # noqa: E402
from cmcoder.llm.factory import chat_model  # noqa: E402
from cmcoder.providers.profiles import resolve_profile  # noqa: E402
from cmcoder.tools.base import ToolContext  # noqa: E402
from cmcoder.tools.files import ReadTool  # noqa: E402
from cmcoder.tools.search import GlobTool, GrepTool  # noqa: E402

missing = [
    n for n in ("CMCODER_BASE_URL", "CMCODER_API_KEY", "CMCODER_MODEL") if not os.environ.get(n)
]
if missing:
    raise SystemExit(f"Set {', '.join(missing)} in your shell first (see m3-graph-by-hand.md).")

_name = os.environ["CMCODER_MODEL"]
_here = Path(os.environ.get("CMCODER_LAB_PROJECT") or Path.cwd()).resolve()
graph = build_graph(
    chat_model(
        _name,
        resolve_profile(_name),
        base_url=os.environ["CMCODER_BASE_URL"],
        api_key=os.environ["CMCODER_API_KEY"],
    ),
    langchain_tools(
        [ReadTool(), GlobTool(), GrepTool()], ToolContext(cwd=_here, project_root=_here)
    ),
)
