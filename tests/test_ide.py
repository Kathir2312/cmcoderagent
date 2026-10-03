"""Editor context for the model (`core/ide.py`)."""

from __future__ import annotations

from pathlib import Path

from cmcoder.core import ide
from cmcoder.protocol.messages import IdeContext, IdeDiagnostic, IdeSelection


def test_nothing_to_say(tmp_path: Path) -> None:
    assert ide.format_ide_context(IdeContext(), tmp_path) is None
    blank = IdeSelection(path=str(tmp_path / "a.py"), start_line=1, end_line=1, text="  \n")
    assert ide.format_ide_context(IdeContext(selection=blank), tmp_path) is None


def test_context_is_relative_capped_and_marked(tmp_path: Path) -> None:
    f = str(tmp_path / "src" / "a.py")
    note = ide.format_ide_context(
        IdeContext(
            active_file=f,
            selection=IdeSelection(path=f, start_line=3, end_line=9, text="x" * 9000),
            diagnostics=[
                IdeDiagnostic(path=f, line=i, severity="warning", message="unused")
                for i in range(40)
            ],
        ),
        tmp_path,
    )
    assert note is not None
    assert note.startswith("<system-reminder>") and note.endswith("</system-reminder>")
    assert "src/a.py open" in note and "lines 3-9 of src/a.py" in note
    assert "[... selection truncated]" in note and "x" * 8001 not in note
    assert note.count("warning: unused") == ide.MAX_DIAGNOSTICS and "… 10 more" in note
    # Files outside the project keep their full path.
    outside = ide.format_ide_context(IdeContext(active_file="/elsewhere/b.py"), tmp_path)
    assert outside is not None and "/elsewhere/b.py open" in outside
