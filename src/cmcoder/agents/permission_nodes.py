"""Permission checking nodes for LangGraph-based agent system."""

from typing import Dict, Any, Optional
from langgraph.graph import StateGraph
from ..core.permissions import PermissionPolicy, PermissionCheck
from .core_state import AgentState

def permission_check_node(state: AgentState) -> AgentState:
    """
    Check permissions for the current operation.

    This node validates that the agent has appropriate permissions
    before proceeding with tool execution or other operations.

    Args:
        state: Current agent state

    Returns:
        Updated agent state
    """
    # Get current tool call info from state
    current_tool_call = state.get("current_tool_call")

    if not current_tool_call:
        # No tool call to check permissions for
        return state

    # In a real implementation, this would check the actual permission policy
    # For demonstration, we'll simulate a permission check

    tool_name = current_tool_call.get("name")

    # Simplified permission check (in practice, this would use the actual policy system)
    allowed_tools = {"Read", "Write", "Edit", "Bash", "Grep"}

    if tool_name in allowed_tools:
        # Permission granted
        return state
    else:
        # Permission denied - add error to state
        error_message = {
            "role": "error",
            "content": f"Permission denied for tool: {tool_name}",
            "type": "permission_denied"
        }

        messages = state.get("messages", [])
        messages.append(error_message)

        updated_state = state.copy()
        updated_state["messages"] = messages
        updated_state["current_tool_call"] = None

        return updated_state

def permission_check_with_policy(policy: PermissionPolicy) -> callable:
    """
    Create a permission check node that uses a specific policy.

    Args:
        policy: Permission policy to use

    Returns:
        Permission checking function
    """
    def check_node(state: AgentState) -> AgentState:
        # Implementation using the specific policy
        return state  # Placeholder implementation

    return check_node

# Export nodes for use in graph construction
PERMISSION_NODES = {
    "check_permissions": permission_check_node
}