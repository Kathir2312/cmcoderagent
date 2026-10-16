"""FastMCP, imported the way cmcoder does (mcp_client.fastmcp_client): a
project's `.env` file doesn't configure it, it doesn't log to the terminal,
and nothing is left in the environment that MCP servers would inherit."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

CHECK = """
from cmcoder.mcp_client import fastmcp_client
fastmcp_client()
import logging, os, fastmcp
print(logging.getLogger("fastmcp").handlers, fastmcp.settings.log_level,
      sorted(k for k in os.environ if k.startswith("FASTMCP")))
"""


def test_a_projects_env_file_does_not_configure_fastmcp(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("FASTMCP_LOG_ENABLED=true\nFASTMCP_LOG_LEVEL=DEBUG\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith("FASTMCP")}
    r = subprocess.run(
        [sys.executable, "-c", CHECK],
        cwd=tmp_path,  # FastMCP looks for .env in the current folder
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "[] INFO []"
