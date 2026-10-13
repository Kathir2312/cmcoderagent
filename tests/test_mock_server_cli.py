"""The standalone mock server (`python -m cmcoder.testing.mock_server`), as the
IDE plugins' tests start it from another process."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import httpx


def test_record_writes_each_chat_request(tmp_path: Path) -> None:
    script = tmp_path / "script.json"
    script.write_text(json.dumps([{"content": "Hi."}]))
    record = tmp_path / "requests.jsonl"
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "cmcoder.testing.mock_server",
            "--script",
            str(script),
            "--port",
            "0",
            "--record",
            str(record),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout
        url = proc.stdout.readline().split("mock server on ")[1].strip()
        body = {"model": "m", "stream": False, "messages": [{"role": "user", "content": "hello ä"}]}
        with httpx.Client(trust_env=False) as client:
            client.post(f"{url}/chat/completions", json=body)
        [line] = record.read_text(encoding="utf-8").splitlines()
        assert json.loads(line)["messages"][0]["content"] == "hello ä"
    finally:
        proc.kill()
        proc.wait()
