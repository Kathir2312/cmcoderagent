"""Images in the terminal: from the clipboard (Ctrl+V / Alt+V), or a pasted or
dropped image file's path (terminals paste a dropped file as its path).

The prompt gets a placeholder, "[Image #1]", for each; an image whose
placeholder the user deletes before sending is left out.
"""

from __future__ import annotations

import io
import os
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

from ..images import MAX_IMAGES, MAX_INPUT_BYTES, Image, ImageError, prepare

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
PLACEHOLDER = re.compile(r"\[Image #(\d+)\]")
# A message about an image ("analyse the image") with none attached: the paste
# didn't work (Windows Terminal and VS Code keep Ctrl+V; a text-only clipboard).
MENTION = re.compile(
    r"\b(?:the|this|that|these|attached|pasted|my)\s+(?:image|screenshot|screen shot|picture"
    r"|snapshot|photo)s?\b|\b(?:image|screenshot)s?\s+(?:attached|above|below)\b",
    re.IGNORECASE,
)
NO_IMAGE_ATTACHED = (
    "No image is attached to this message. Press Alt+V to paste the clipboard's image "
    "(Ctrl+V pastes only text in Windows Terminal and VS Code), drop the image file "
    "here, or type /image. Press Enter again to send the message as it is."
)


def only_placeholders(text: str) -> bool:
    """`/image [Image #1]`: the file was dropped after typing /image, so it's attached."""
    return bool(PLACEHOLDER.search(text)) and not PLACEHOLDER.sub("", text).strip()


def mentions_image(text: str) -> bool:
    return bool(MENTION.search(PLACEHOLDER.sub("", text)))


def no_clipboard_image() -> str:
    """What to do when the clipboard has no image."""
    how = {"nt": " (Win+Shift+S copies a screenshot)"}.get(os.name, "")
    if sys.platform.startswith("linux"):
        how = " (on Linux, reading it needs wl-paste or xclip)"
    return f"No image in the clipboard{how}. Copy one, or attach a file: /image path/to/shot.png"


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


def read_image(arg: str) -> tuple[bytes, str]:
    """/image [path]: the clipboard's image, or an image file; ImageError if there isn't one."""
    if not arg:
        raw = clipboard_image()
        if raw is None:
            raise ImageError(no_clipboard_image())
        return raw, ""
    path = image_path(arg)
    if path is None:
        limit = MAX_INPUT_BYTES // (1024 * 1024)
        raise ImageError(f"Not an image file (PNG, JPEG, GIF or WebP, up to {limit} MB): {arg}")
    return path.read_bytes(), path.name


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
