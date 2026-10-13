"""cmcoder: agentic coding assistant for OpenAI-compatible endpoints."""

from pathlib import Path

__version__ = "0.1.0"


def _build() -> str:
    """Which build this is: "<commit> <date>" from the standalone build
    (packaging/build.py writes cmcoder/BUILD), else "source"."""
    try:
        return (Path(__file__).resolve().parent / "BUILD").read_text(encoding="utf-8").strip()
    except OSError:
        return "source"


BUILD = _build()
VERSION_TEXT = f"{__version__} ({BUILD})"
