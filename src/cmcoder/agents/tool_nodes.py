"""Tool execution nodes for LangGraph-based agent system."""

import asyncio
from typing import Dict, Any, Optional, List
from langgraph.graph import StateGraph
from langgraph.checkpoint import MemorySaver
from ..tools.base import ToolContext, ToolResult
from ..tools import registry
from ..tools.base import Tool
from ..core.permissions import PermissionCheck, Decision
from ..core.permissions import PermissionPolicy
from .core_state import AgentState

# Import all available tools
DEFAULT_TOOLS = registry.default_tools()

def tool_execution_node(state: AgentState) -> AgentState:
    """
    Execute a tool call based on the current tool call in state.
    This is the primary tool execution node.
    """
    # Get the current tool call
    current_tool_call = state.get("current_tool_call")
    if not current_tool_call:
        return state

    tool_name = current_tool_call.get("name")
    tool_args = current_tool_call.get("arguments", {})

    # Find the appropriate tool
    tool = None
    for available_tool in DEFAULT_TOOLS:
        if available_tool.name == tool_name:
            tool = available_tool
            break

    if not tool:
        # Handle unknown tool
        error_result = ToolResult(
            f"Unknown tool {tool_name!r}. Available tools: {', '.join([t.name for t in DEFAULT_TOOLS])}",
            is_error=True
        )
        return _append_tool_result(state, current_tool_call, error_result)

    # Validate tool arguments using Pydantic
    try:
        args = tool.Input.model_validate(tool_args)
    except Exception as e:
        error_result = ToolResult(f"Invalid tool call arguments: {str(e)}", is_error=True)
        return _append_tool_result(state, current_tool_call, error_result)

    # Set up tool context
    ctx = ToolContext(
        cwd=state.get("cwd", "/"),
        project_root=state.get("project_root", "/"),
        max_output_chars=state.get("max_output_chars", 30000)
    )

    # Execute the tool asynchronously
    try:
        tool_result = asyncio.run(tool.run(args, ctx))
        return _append_tool_result(state, current_tool_call, tool_result)
    except Exception as e:
        error_result = ToolResult(f"Tool execution failed: {str(e)}", is_error=True)
        return _append_tool_result(state, current_tool_call, error_result)

def _append_tool_result(state: AgentState, tool_call: Dict[str, Any], result: ToolResult) -> AgentState:
    """Helper function to append tool execution results to state."""
    # Create a message for the tool result
    result_message = {
        "role": "tool",
        "content": result.content,
        "tool_call_id": tool_call.get("id"),
        "name": tool_call.get("name"),
        "is_error": result.is_error,
        "summary": result.summary
    }

    # Add the tool result to messages
    new_messages = state.get("messages", []).copy()
    new_messages.append(result_message)

    # Update state with the new messages
    updated_state = state.copy()
    updated_state["messages"] = new_messages
    updated_state["current_tool_call"] = None

    return updated_state

def permission_check_node(state: AgentState) -> AgentState:
    """
    Check if the current tool call is allowed based on permissions.
    """
    current_tool_call = state.get("current_tool_call")
    if not current_tool_call:
        return state

    tool_name = current_tool_call.get("name")
    tool_args = current_tool_call.get("arguments", {})

    # In a real implementation, we would use the actual permission policy
    # This is simplified for demonstration
    allowed = True
    reason = ""

    if not allowed:
        # Construct permission denied message
        permitted_tools = [tool.name for tool in DEFAULT_TOOLS]
        error_msg = f"Permission denied for tool {tool_name}. Available tools: {', '.join(permitted_tools)}"

        # Add to messages
        error_result = ToolResult(error_msg, is_error=True)
        state = _append_tool_result(state, current_tool_call, error_result)

    return state

# Create nodes dictionary for easy reference
TOOL_NODES = {
    "tool_execution": tool_execution_node,
    "permission_check": permission_check_node
}