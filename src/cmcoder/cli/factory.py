"""Factory for creating agent instances with LangGraph integration."""

import asyncio
from typing import Optional
from ..core.agent import Agent
from ..core.subagents import SubagentRuntime
from ..core.permissions import PermissionPolicy
from ..agents.core_graph import create_agent_graph
from ..agents.subagent_graph import SubagentManager
from ..core.commands import expand_commands

async def create_langgraph_agent(
    model: str,
    provider: str,
    system_prompt: str,
    permission_policy: Optional[PermissionPolicy] = None,
    session_id: Optional[str] = None
) -> Agent:
    """
    Create a new agent instance using LangGraph for the core engine.

    Args:
        model: The LLM model to use
        provider: The provider for the model
        system_prompt: The system prompt for the agent
        permission_policy: Optional permission policy for tool access
        session_id: Optional session identifier

    Returns:
        Configured Agent instance
    """
    # Create the main agent graph
    agent_graph = create_agent_graph(model, provider, system_prompt)

    # Create subagent manager
    subagent_manager = SubagentManager()

    # Create the agent with LangGraph components
    agent = Agent(
        model=model,
        provider=provider,
        system_prompt=system_prompt,
        permission_policy=permission_policy,
        session_id=session_id,
        graph=agent_graph,
        subagent_manager=subagent_manager
    )

    return agent

async def create_default_agent(session_id: Optional[str] = None) -> Agent:
    """
    Create a default agent instance with standard configuration.

    Args:
        session_id: Optional session identifier

    Returns:
        Configured Agent instance
    """
    return await create_langgraph_agent(
        model="gpt-4",
        provider="openai",
        system_prompt="You are a helpful coding assistant.",
        session_id=session_id
    )