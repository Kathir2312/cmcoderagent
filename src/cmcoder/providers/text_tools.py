"""Tool calls written as text, in the Hermes/Qwen format.

Qwen's chat template has the model write tool calls as

    <tool_call>
    {"name": "Read", "arguments": {"file_path": "app.py"}}
    </tool_call>

A server with a tool-call parser (vLLM --enable-auto-tool-choice
--tool-call-parser hermes) turns that into native `tool_calls`. Without one,
the text arrives as ordinary content. cmcoder handles both cases:

- "native" models: if a reply has no native tool calls but contains these
  tags for known tools, they are parsed into tool calls (`extract`);
- "prompted" models (profile toolCalling: "prompted", for servers that reject
  the `tools` parameter): the tools are described in the system prompt in the
  same format, and the history is sent as text (`to_prompted_wire`).
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from .content import user_content
from .messages import Message, ToolCall, ToolSpec

OPEN, CLOSE = "<tool_call>", "</tool_call>"
_BLOCK = re.compile(r"<tool_call>\s*(.*?)\s*(?:</tool_call>|$)", re.S)


def _loads(text: str) -> Any:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    for candidate in (text, re.sub(r",\s*([}\]])", r"\1", text)):
        try:
            return json.loads(candidate, strict=False)
        except ValueError:
            continue
    return None


def extract(content: str, known_tools: set[str]) -> tuple[str, list[ToolCall]]:
    """Split text tool calls out of `content`: (remaining text, calls).

    Only blocks naming a known tool with valid JSON count; anything else stays
    in the text, so prose that merely mentions <tool_call> is left alone."""
    calls: list[ToolCall] = []
    kept: list[str] = []
    pos = 0
    for m in _BLOCK.finditer(content):
        data = _loads(m.group(1))
        name = data.get("name") if isinstance(data, dict) else None
        if not isinstance(data, dict) or name not in known_tools:
            continue
        args = data.get("arguments", data.get("parameters", {}))
        arguments = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
        calls.append(ToolCall(f"call_{uuid.uuid4().hex[:12]}", str(name), arguments))
        kept.append(content[pos : m.start()])
        pos = m.end()
    if not calls:
        return content, []
    kept.append(content[pos:])
    return "".join(kept).strip(), calls


class Holdback:
    """Stream filter: pass text through until a <tool_call> tag starts, then
    hold everything back, so tool-call JSON isn't shown as the reply."""

    def __init__(self) -> None:
        self.held = ""
        self.inside = False

    def feed(self, text: str) -> str:
        if self.inside:
            self.held += text
            return ""
        buf = self.held + text
        i = buf.find(OPEN)
        if i >= 0:
            self.inside = True
            self.held = buf[i:]
            return buf[:i]
        # Keep a possible partial "<tool_ca" at the end until we know.
        for k in range(min(len(OPEN) - 1, len(buf)), 0, -1):
            if OPEN.startswith(buf[-k:]):
                self.held = buf[-k:]
                return buf[:-k]
        self.held = ""
        return buf

    def flush(self) -> str:
        """At the end of the reply: text held back that wasn't a tag after all."""
        out = "" if self.inside else self.held
        self.held = ""
        return out


def tools_prompt(tools: list[ToolSpec]) -> str:
    """The tool description Qwen's own chat template uses."""
    lines = [
        json.dumps(
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            },
            ensure_ascii=False,
        )
        for t in tools
    ]
    return (
        "# Tools\n\nYou may call one or more functions to assist with the user query.\n\n"
        "You are provided with function signatures within <tools></tools> XML tags:\n<tools>\n"
        + "\n".join(lines)
        + "\n</tools>\n\nFor each function call, return a json object with function name and "
        "arguments within <tool_call></tool_call> XML tags:\n<tool_call>\n"
        '{"name": <function-name>, "arguments": <args-json-object>}\n</tool_call>'
    )


def to_prompted_wire(
    messages: list[Message], tools: list[ToolSpec], vision: bool = False
) -> list[dict[str, Any]]:
    """The conversation for a server that gets no `tools` parameter: tools in
    the system prompt, calls and results as text."""
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "system":
            out.append({"role": "system", "content": f"{m.content}\n\n{tools_prompt(tools)}"})
        elif m.role == "assistant":
            parts = [m.content] if m.content else []
            for c in m.tool_calls:
                args = _loads(c.arguments) if c.arguments else {}
                payload = {"name": c.name, "arguments": args if args is not None else c.arguments}
                parts.append(f"{OPEN}\n{json.dumps(payload, ensure_ascii=False)}\n{CLOSE}")
            out.append({"role": "assistant", "content": "\n".join(parts)})
        elif m.role == "tool":
            block = f"<tool_response>\n{m.content}\n</tool_response>"
            prev = out[-1] if out else None
            if prev and prev["role"] == "user" and prev.get("_tool_results"):
                prev["content"] += "\n" + block  # several results: one user message
            else:
                out.append({"role": "user", "content": block, "_tool_results": True})
        elif m.role == "user":
            out.append({"role": "user", "content": user_content(m, vision)})
        else:
            out.append({"role": m.role, "content": m.content})
    if not any(m["role"] == "system" for m in out):
        out.insert(0, {"role": "system", "content": tools_prompt(tools)})
    for m in out:
        m.pop("_tool_results", None)
    return out
