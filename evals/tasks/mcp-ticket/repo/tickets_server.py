"""A tiny ticket tracker as an MCP server (for the mcp-ticket eval)."""

from mcp.server.mcpserver import MCPServer

server = MCPServer("tickets")

TICKETS = {
    "ABC-41": "Title: Fix the login timeout\nUsers are logged out after 5 minutes.",
    "ABC-42": "Title: Add a --version flag\nThe CLI should print its version and exit.",
}


@server.tool()
def get_ticket(ticket_id: str) -> str:
    """Get a ticket's title and description by its id (e.g. ABC-1)."""
    return TICKETS.get(ticket_id, f"No ticket {ticket_id}.")


if __name__ == "__main__":
    server.run("stdio")
