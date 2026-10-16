"""A small MCP server for the tests: tools, a resource and a prompt.

Run: python demo_server.py [stdio|streamable-http] [port]
"""

from __future__ import annotations

import os
import sys

from mcp.server.mcpserver import MCPServer

server = MCPServer("demo", instructions="Demo server for cmcoder's tests.")


@server.tool()
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


@server.tool()
def shout(text: str) -> str:
    """Upper-case some text."""
    return text.upper()


@server.tool()
def fail(reason: str) -> str:
    """Always fails (for error handling tests)."""
    raise ValueError(f"failed on purpose: {reason}")


@server.tool()
def env(name: str) -> str:
    """Read an environment variable of the server process."""
    return os.environ.get(name, "<unset>")


@server.resource("demo://greeting")
def greeting() -> str:
    """A fixed greeting."""
    return "Hello from the demo server."


@server.prompt()
def review(file: str) -> str:
    """Ask for a code review of a file."""
    return f"Please review {file} for bugs."


if __name__ == "__main__":
    transport = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    if transport == "stdio":
        server.run("stdio")
    else:
        server.run("streamable-http", port=int(sys.argv[2]), host="127.0.0.1")  # type: ignore[call-overload]
