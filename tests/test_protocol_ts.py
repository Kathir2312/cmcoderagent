"""The extension's TypeScript protocol types must match the Python models."""

from __future__ import annotations

from pathlib import Path

from cmcoder.protocol.typescript import generate_typescript, ts_type

GENERATED = Path(__file__).parent.parent / "vscode" / "src" / "protocol.ts"


def test_protocol_ts_is_up_to_date() -> None:
    assert GENERATED.read_text(encoding="utf-8") == generate_typescript(), (
        "vscode/src/protocol.ts is out of date. Run: "
        "uv run cmcoder protocol-schema --typescript > vscode/src/protocol.ts"
    )


def test_generated_types() -> None:
    ts = generate_typescript()
    assert "export const PROTOCOL_VERSION = 1;" in ts
    # Events always carry every field; client messages may leave defaults out.
    assert "  is_error: boolean;" in ts
    assert "  remember?: boolean;" in ts
    assert "export type AgentEvent =" in ts and "export type ClientMessage =" in ts


def test_ts_type() -> None:
    assert ts_type({"type": "array", "items": {"type": "string"}}) == "string[]"
    assert ts_type({"anyOf": [{"type": "string"}, {"type": "null"}]}) == "string | null"
    assert ts_type({"enum": ["a", "b"]}) == '"a" | "b"'
    assert ts_type({"type": "array", "items": {"enum": ["a", "b"]}}) == '("a" | "b")[]'
    assert ts_type({"type": "object"}) == "Record<string, unknown>"
