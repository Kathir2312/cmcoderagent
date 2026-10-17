"""State definition for the LangGraph-based agent system."""

from typing import List, Optional, Dict, Any
from typing_extensions import TypedDict
from pydantic import BaseModel
from ..protocol import events as ev
from ..providers.messages import Message

class AgentState(TypedDict):
    """The complete state of an agent for LangGraph."""
    # Core conversation data
    messages: List[Message]

    # Tool execution state
    current_tool_call: Optional[Dict[str, Any]]

    # Permission system state
    permission_check: Optional[Dict[str, Any]]

    # Context management
    context_window: int
    max_output_chars: int

    # Subagent state
    subagents: List[Dict[str, Any]]

    # Session management
    session_id: str
    turn: int

    # Agent configuration
    model: str
    provider: str
    system_prompt: str

    # UI state
    ui_events: List[ev.Event]

    # Critique state
    critique_active: bool
    critique_rounds: int
    critique_issues: List[Dict[str, Any]]

    # Completion state
    is_complete: bool