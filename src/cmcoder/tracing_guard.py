"""Keeps LangChain from sending cmcoder's runs to LangSmith or Langfuse on
its own.

LangChain traces every run to LangSmith's cloud by itself when
`LANGSMITH_TRACING` (or the older `LANGCHAIN_TRACING_V2`) is set in the
environment, as it may be in a developer's shell, and the runs carry the
code cmcoder read. It reads the variables once, when it's first imported,
so clearing them later doesn't stop it (Phase 8 spike, E3). So cmcoder takes
them out of its own environment first thing (`cmcoder/__init__.py`), before
anything can import LangChain, and keeps them aside:

- the commands, hooks and tests cmcoder runs for the developer get them
  back (`child_env`), so their own LangChain projects still trace;
- tracing that settings allow (Phase 8, M8) gets them from here.
"""

from __future__ import annotations

import os
from collections.abc import MutableMapping

# Names LangSmith (and LangChain before it) and Langfuse read their switches,
# keys and addresses from.
PREFIXES = ("LANGSMITH_", "LANGCHAIN_", "LANGFUSE_")

_held: dict[str, str] = {}


def hold(environ: MutableMapping[str, str] = os.environ) -> None:
    """Move the tracing variables out of `environ` into this module."""
    for name in list(environ):
        if name.upper().startswith(PREFIXES):
            _held[name] = environ.pop(name)


def held() -> dict[str, str]:
    """The tracing variables cmcoder was started with (for child processes)."""
    return dict(_held)
