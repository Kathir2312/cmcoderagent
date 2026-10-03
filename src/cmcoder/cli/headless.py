"""Non-interactive mode: `cmcoder -p "prompt"`."""

from __future__ import annotations

import sys
from typing import Literal

from ..config.settings import Settings, ignored_settings_message
from ..protocol import events as ev
from .factory import AgentOptions, build_agent

OutputFormat = Literal["text", "json", "stream-json"]


def _emit(event: ev.Event) -> None:
    sys.stdout.write(event.model_dump_json() + "\n")
    sys.stdout.flush()


async def run_headless(
    settings: Settings, opts: AgentOptions, prompt: str, output_format: OutputFormat, verbose: bool
) -> int:
    opts.ask = None  # nobody to ask: ASK decisions become denials with a hint
    opts.persist_rules = False
    agent = await build_agent(settings, opts)
    if warning := ignored_settings_message(settings):
        print(f"warning: {warning}", file=sys.stderr)
    final: ev.Result | None = None
    try:
        if output_format == "stream-json":
            _emit(agent.init_event())
        async for event in agent.run(prompt):
            if output_format == "stream-json":
                _emit(event)
            if isinstance(event, ev.Result):
                final = event
            elif isinstance(event, ev.Error):
                hint = f"\n  hint: {event.hint}" if event.hint else ""
                print(f"error: {event.message}{hint}", file=sys.stderr)
            elif isinstance(event, ev.Warning):
                print(f"warning: {event.message}", file=sys.stderr)
            elif isinstance(event, ev.Compacted) and output_format != "stream-json":
                print(
                    f"note: context nearly full; summarised {event.summarized_messages} earlier "
                    f"messages with {event.model}",
                    file=sys.stderr,
                )
            elif verbose and isinstance(event, ev.ToolUse):
                print(f"● {event.label}", file=sys.stderr)
            elif verbose and isinstance(event, ev.ToolResult):
                status = "error" if event.is_error else (event.summary or "done")
                print(f"  └ {status}", file=sys.stderr)
            elif isinstance(event, ev.PermissionDenied) and output_format != "stream-json":
                print(f"permission denied: {event.reason}", file=sys.stderr)
    finally:
        await agent.close()
    if final is None:
        return 1
    if output_format == "json":
        _emit(final)
    elif output_format == "text" and final.result:
        print(final.result)
    return 1 if final.is_error else 0
