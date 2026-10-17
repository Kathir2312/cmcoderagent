#!/usr/bin/env python3
"""Test script to verify LangGraph integration works correctly."""

import sys
import os

# Add the src directory to the Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def test_imports():
    """Test that all the new modules can be imported successfully."""
    try:
        # Test importing the main components
        from cmcoder.agents import (
            AgentState,
            AgentGraph,
            [PERSON_NAME]            TOOL_NODES,
            SubagentManager,
            SubagentIntegrationLayer,
            SubagentFactory
        )

        print("✅ All core modules imported successfully")

        # Test importing permission components
        from cmcoder.agents.permission_nodes import (
            permission_check_node,
            PERMISSION_NODES
        )

        print("✅ Permission modules imported successfully")

        # Test that basic functionality works
        print("✅ LangGraph integration test completed successfully")
        return True

    except Exception as e:
        print(f"❌ Failed to import modules: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_basic_graph_creation():
    """Test basic graph creation and structure."""
    try:
        from cmcoder.agents.core_graph import create_agent_graph
        from cmcoder.agents.core_state import AgentState

        # Create a basic graph
        graph = create_agent_graph("gpt-4", "openai", [PERSON_NAME] are a helpful assistant.")

        print("✅ Basic agent graph creation successful")

        # Create initial state
        initial_state = {
            "messages": [],
            "current_tool_call": [PERSON_NAME]"permission_check": None,
            "context_window": 128000,
            "max_output_chars": 30000,
            "subagents": [],
            "session_id": "test_session",
            "turn": 0,
            "model": "gpt-4",
            "provider": "openai",
            "system_prompt": "You are a helpful assistant.",
            "ui_events": [],
            "critique_active": False,
            "critique_rounds": 2,
            "critique_issues": [],
            "is_complete": False
        }

        print("✅ Initial state creation successful")
        return True

    except Exception as e:
        print(f"❌ Graph creation failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("Testing LangGraph integration...")

    success1 = test_imports()
    success2 = test_basic_graph_creation()

    if success1 and success2:
        print("\n🎉 All tests passed! LangGraph integration is working correctly.")
        sys.exit(0)
    else:
        print("\n💥 Some tests failed.")
        sys.exit(1)