"""Images in the terminal: from the clipboard (Ctrl+V / Alt+V), or a pasted or
dropped image file's path (terminals paste a dropped file as its path).

The prompt gets a placeholder, "[Image #1]", for each; an image whose
placeholder the user deletes before sending is left out.
"""

from __future__ import annotations

import io
import os
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from ..images import MAX_IMAGES, MAX_INPUT_BYTES, Image, ImageError, prepare

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
PLACEHOLDER = re.compile(r"\[Image #(\d+)\]")


def clipboard_image() -> bytes | None:
    """The clipboard's image as PNG bytes, or None (no image, or no way to read
    it: Linux needs wl-paste or xclip, as Pillow uses them)."""
    try:
        from PIL import ImageGrab
    except ImportError:
        return None
    try:
        grabbed = ImageGrab.grabclipboard()
    except Exception:  # no clipboard tool, no display, a busy clipboard
        return None
    if grabbed is None:
        return None
    if isinstance(grabbed, list):  # Windows: files copied in Explorer
        for name in grabbed:
            path = image_path(str(name))
            if path is not None:
                return path.read_bytes()
        return None
    out = io.BytesIO()
    grabbed.save(out, "PNG")
    return out.getvalue()


def image_path(text: str) -> Path | None:
    """The image file a pasted text names (a dropped file's path, quoted or
    with escaped spaces, or a file:// URL), or None if it isn't one."""
    text = text.strip()
    if not text or "\n" in text or len(text) > 4096:
        return None
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1]
    if text.startswith("file://"):
        text = unquote(urlparse(text).path)
        if re.match(r"^/[A-Za-z]:/", text):  # file:///C:/x.png
            text = text[1:]
    elif os.name != "nt" and "\\ " in text:  # macOS/Linux terminals: "my\ shot.png"
        text = re.sub(r"\\(.)", r"\1", text)  # (on Windows, \ separates folders)
    path = Path(text).expanduser()
    if path.suffix.lower() not in IMAGE_SUFFIXES:
        return None
    try:
        if not path.is_file() or path.stat().st_size > MAX_INPUT_BYTES:
            return None
    except OSError:
        return None
    return path


class PendingImages:
    """The images attached to the message being typed, by placeholder number."""

    def __init__(self) -> None:
        self.images: dict[int, Image] = {}

    def add(self, raw: bytes, name: str = "") -> str:
        """Checks the image and returns its placeholder; ImageError if it can't be used."""
        if len(self.images) >= MAX_IMAGES:
            raise ImageError(f"At most {MAX_IMAGES} images per message.")
        image = prepare(raw, name)
        number = max(self.images, default=0) + 1
        self.images[number] = image
        return f"[Image #{number}]"

    def take(self, line: str) -> list[Image]:
        """The images whose placeholders are still in `line`, in order; then empty."""
        kept = [int(n) for n in PLACEHOLDER.findall(line)]
        out = [self.images[n] for n in dict.fromkeys(kept) if n in self.images]
        self.images.clear()
        return out
