"""OpenTelemetry metrics (Phase 4 item 4), against a stand-in OTLP/HTTP collector."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from cmcoder.cli.doctor import Doctor
from cmcoder.config.settings import (
    Settings,
    TelemetryConfig,
    load_settings,
    set_project_trust,
)
from cmcoder.core.agent import Agent
from cmcoder.core.permissions import PermissionPolicy
from cmcoder.providers.profiles import resolve_profile
from cmcoder.telemetry import Telemetry, from_settings, tool_label
from cmcoder.tools.base import ToolContext
from cmcoder.tools.registry import default_tools

from .conftest import make_provider

SECRET_PROMPT = "please refactor the zebra-unicorn-4711 module"


class Collector:
    def __init__(self) -> None:
        self.posts: list[tuple[dict[str, str], dict[str, Any]]] = []
        self.status = 200
        collector = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a: Any) -> None:
                pass

            def do_POST(self) -> None:
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                collector.posts.append((dict(self.headers), json.loads(raw)))
                self.send_response(collector.status)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def metrics(self) -> dict[str, list[dict[str, Any]]]:
        """The last export: metric name -> data points (attributes as a dict)."""
        _, body = self.posts[-1]
        out: dict[str, list[dict[str, Any]]] = {}
        for rm in body["resourceMetrics"]:
            for sm in rm["scopeMetrics"]:
                for m in sm["metrics"]:
                    for p in m["sum"]["dataPoints"]:
                        attrs = {a["key"]: a["value"]["stringValue"] for a in p["attributes"]}
                        value = float(p.get("asDouble", p.get("asInt", 0)))
                        out.setdefault(m["name"], []).append({**attrs, "value": value})
        return out


@pytest.fixture
def collector() -> Iterator[Collector]:
    c = Collector()
    yield c
    c.httpd.shutdown()
    c.httpd.server_close()


def cfg(**kw: Any) -> TelemetryConfig:
    return TelemetryConfig.model_validate(kw)


async def test_a_session_is_counted_without_its_content(
    mock_server: Any, project: Path, collector: Collector, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OTEL_TOKEN", "t0ken")
    (project / "a.txt").write_text("hello\n")
    server = mock_server(
        [
            {"tool_calls": [{"name": "Read", "arguments": {"file_path": "a.txt"}}]},
            {"content": "Refactored the zebra-unicorn-4711 module.", "cost": 0.25},
        ]
    )
    telemetry = from_settings(
        cfg(
            enabled=True,
            endpoint=collector.url,
            headers={"Authorization": "Bearer ${OTEL_TOKEN}"},
            resourceAttributes={"team": "payments"},
        ),
        environ={},
    )
    assert telemetry is not None
    telemetry.session_started("cli", "s-1")
    agent = Agent(
        make_provider(server),
        "qwen3-27b",
        resolve_profile("qwen3-27b"),
        default_tools(),
        PermissionPolicy("default"),
        ToolContext(cwd=project, project_root=project),
        "test",
        telemetry=telemetry,
    )
    try:
        [e async for e in agent.run(SECRET_PROMPT)]
    finally:
        await agent.close()  # exports what's left

    headers, body = collector.posts[-1]
    assert headers["Authorization"] == "Bearer t0ken"
    assert "zebra" not in json.dumps(body) and "a.txt" not in json.dumps(body)
    resource = {
        a["key"]: a["value"]["stringValue"]
        for a in body["resourceMetrics"][0]["resource"]["attributes"]
    }
    assert resource["service.name"] == "cmcoder" and resource["team"] == "payments"
    assert len(resource["cmcoder.installation.id"]) == 36

    m = collector.metrics()
    assert m["cmcoder.session.count"][0]["frontend"] == "cli"
    tokens = {p["type"]: p["value"] for p in m["cmcoder.token.usage"]}
    assert tokens["input"] > 0 and tokens["output"] > 0
    assert m["cmcoder.cost.usage"][0]["value"] == 0.25
    assert {(p["tool"], p["result"]) for p in m["cmcoder.tool.calls"]} == {("Read", "ok")}
    assert m["cmcoder.turn.count"][0]["result"] == "success"
    assert m["cmcoder.turn.duration"][0]["value"] > 0
    assert {p["session.id"] for p in m["cmcoder.turn.count"]} == {agent.session_id}


def test_off_unless_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    assert from_settings(cfg(), environ={}) is None
    assert from_settings(cfg(enabled=True), environ={}) is None  # no endpoint
    env = {
        "CMCODER_TELEMETRY": "1",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://otel:4318/",
        "OTEL_EXPORTER_OTLP_HEADERS": "x-api-key=abc%3D,other=1",
        "OTEL_RESOURCE_ATTRIBUTES": "department=it",
        "OTEL_METRIC_EXPORT_INTERVAL": "30000",
    }
    t = from_settings(cfg(), environ=env)
    assert t is not None
    assert t.url == "http://otel:4318/v1/metrics" and t.headers["x-api-key"] == "abc="
    assert t.resource["department"] == "it" and t.interval == 30


async def test_a_collector_that_fails_never_disturbs(collector: Collector) -> None:
    collector.status = 500
    t = from_settings(cfg(enabled=True, endpoint=collector.url), environ={})
    assert t is not None
    t.add("cmcoder.turn.count", 1, {"model": "m"})
    assert await t.export() is False and t.last_error and "500" in t.last_error
    await t.client.aclose()
    t2 = from_settings(cfg(enabled=True, endpoint="http://127.0.0.1:1"), environ={})
    assert t2 is not None
    t2.add("cmcoder.turn.count", 1, {"model": "m"})
    assert await t2.export() is False
    await t2.close()  # still fine


def test_mcp_tools_are_grouped_by_server() -> None:
    assert tool_label("mcp__github__create_issue") == "mcp__github"
    assert tool_label("Bash") == "Bash"


def test_a_repository_cannot_send_your_metrics_elsewhere(project: Path) -> None:
    (project / ".cmcoder").mkdir()
    (project / ".cmcoder" / "settings.json").write_text(
        '{"telemetry": {"enabled": true, "endpoint": "https://collector.attacker.example"}}'
    )
    s = load_settings(project, environ={})
    assert s.telemetry.endpoint is None
    assert any("telemetry" in x for x in s.ignored_project_settings)
    set_project_trust(project, True)
    assert load_settings(project, environ={}).telemetry.endpoint is not None


async def test_doctor(collector: Collector) -> None:
    console = Console(record=True, width=200)
    s = Settings.model_validate({"telemetry": {"enabled": True, "endpoint": collector.url}})
    await Doctor(s, console).check_telemetry()
    assert "OpenTelemetry metrics go to" in console.export_text()
    assert collector.posts[-1][1] == {"resourceMetrics": []}  # no fake metric sent
    assert isinstance(Telemetry, type)
