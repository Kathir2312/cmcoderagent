"""Phase 5 item 5: code search through the VS Code protocol (the setup flow,
indexing with progress, /index in the panel, and the status bar's state)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .test_rag_index import AUTH
from .test_stdio import Agent


async def test_set_up_and_use_code_search_from_vs_code(mock_server: Any, project: Path) -> None:
    (project / "auth.py").write_text(AUTH)
    server = mock_server([{"content": "It's refresh_auth_token."}])
    agent = await Agent.start(project, server)
    try:
        await agent.until("system_init")
        await agent.send(type="index", action="status")
        status = await agent.until("index_status")
        assert status["set_up"] is False and status["active"] is False
        # The setup flow: the gateway's models, then the choice.
        await agent.send(type="rag_candidates")
        candidates = await agent.until("rag_candidates")
        assert "default:text-embedding-3-small" not in candidates["other"]
        assert candidates["errors"] == {}
        await agent.send(type="rag_setup", embedding_model="default:qwen3-27b")
        bad = await agent.until("rag_setup_result")
        assert bad["ok"] is False and "embedding model" in bad["message"]
        await agent.send(
            type="rag_setup",
            embedding_model="text-embedding-3-small",
            store="local",
            index_now=True,
        )
        progress = await agent.until("index_progress")
        assert progress["total"] == 1
        done = await agent.until("rag_setup_result")
        assert done["ok"] is True and "Indexed 1 files" in done["message"]
        status = await agent.until("index_status")
        assert status["set_up"] and status["active"] and status["files"] == 1
        assert any("Automatic context" in line for line in status["lines"])
        saved = json.loads((Path(os.environ["CMCODER_CONFIG_DIR"]) / "settings.json").read_text())
        assert saved["rag"]["embeddingModel"] == "default:text-embedding-3-small"
        # The session searches it from now on: automatic context on the next message.
        await agent.send(type="user_message", text="how is the expired auth token refreshed?")
        context = await agent.until("code_context")
        assert context["items"][0]["path"] == "auth.py"
        await agent.until("result")
        # /index in the panel answers there, and an update changes nothing.
        await agent.send(type="user_message", text="/index")
        reply = await agent.until("assistant_message")
        assert "Code index" in reply["text"] and "1 unchanged" in reply["text"]
        await agent.until("result")
        await agent.send(type="index", action="clear")
        while (await agent.until("index_status"))["files"] != 0:
            pass  # earlier status updates (the panel's /index sends one) come first
    finally:
        await agent.send(type="shutdown")
        await agent.proc.wait()


async def test_an_api_key_in_a_rejected_message_is_never_echoed(
    mock_server: Any, project: Path
) -> None:
    """Phase 6 security review: the IDEs' code search set-up sends a Chroma API
    key; a message cmcoder rejects is reported by field and reason, never with
    its values (which would reach the chat and the IDE's log)."""
    secret = "sk-chroma-secret-9f3a"
    agent = await Agent.start(project, mock_server([]))
    try:
        await agent.until("system_init")
        # embedding_model must be a string; the rest of the message carries the key.
        await agent.send_raw(
            json.dumps(
                {
                    "type": "rag_setup",
                    "embedding_model": 5,
                    "store": "chroma-server",
                    "api_key": secret,
                }
            )
            + "\n"
        )
        error = await agent.until("error")
        assert "embedding_model" in error["message"] and secret not in json.dumps(error)
    finally:
        await agent.send(type="shutdown")
        await agent.proc.wait()
    assert secret not in await agent.stderr()
    assert all(secret not in json.dumps(e) for e in agent.events)
