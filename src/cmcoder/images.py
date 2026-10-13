"""Images the user attaches to a message: pasted or dropped in an IDE's chat, or
pasted in the terminal.

An image is checked by its bytes, never by a name or a declared type: only PNG,
JPEG, GIF and WebP are taken. A large one is made smaller (at most MAX_SIDE
pixels on its longer side), which keeps the request small and the model's
image tokens few. It travels as base64 on the user message, is saved with the
session, and goes to the gateway as an OpenAI-style image part, or, for a model
that can't see images, as the description a vision model wrote (core/vision.py).
"""

from __future__ import annotations

import base64
import binascii
import io
import warnings
from dataclasses import dataclass

MAX_IMAGES = 5  # per message
MAX_INPUT_BYTES = 20 * 1024 * 1024  # what we accept before making it smaller
MAX_BYTES = 4 * 1024 * 1024  # what we send, per image
MAX_SIDE = 1568  # pixels, the longer side
MAX_PIXELS = 50_000_000  # refuse "decompression bombs" before decoding them
TOKENS_PER_IMAGE = 1600  # a fair estimate for a MAX_SIDE image on vision models

_MAGIC: list[tuple[bytes, str]] = [
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
]


class ImageError(ValueError):
    """An image cmcoder can't take, with a message for the user."""


@dataclass
class Image:
    media_type: str  # image/png, image/jpeg, image/gif or image/webp
    data: str  # base64
    name: str = ""  # a file name, or "" for a pasted image
    width: int = 0
    height: int = 0
    # Written by the vision model when the main model can't see images.
    description: str = ""
    described_by: str = ""

    @property
    def data_url(self) -> str:
        return f"data:{self.media_type};base64,{self.data}"

    def label(self, number: int) -> str:
        return f"Image #{number}" + (f" ({self.name})" if self.name else "")


def sniff(raw: bytes) -> str | None:
    """The media type from the file's first bytes, or None if it isn't an image we take."""
    for magic, media_type in _MAGIC:
        if raw.startswith(magic):
            return media_type
    if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return None


def prepare(raw: bytes, name: str = "") -> Image:
    """An Image from a file's bytes: checked, and made smaller if it's large."""
    if len(raw) > MAX_INPUT_BYTES:
        raise ImageError(f"{name or 'The image'} is larger than {MAX_INPUT_BYTES // 2**20} MB.")
    media_type = sniff(raw)
    if media_type is None:
        raise ImageError(f"{name or 'That'} isn't a PNG, JPEG, GIF or WebP image.")
    try:
        from PIL import Image as Pil
    except ImportError:  # running from source without Pillow: send as it is if small enough
        if len(raw) > MAX_BYTES:
            raise ImageError(
                f"{name or 'The image'} is larger than {MAX_BYTES // 2**20} MB."
            ) from None
        return Image(media_type, base64.b64encode(raw).decode("ascii"), name)
    try:
        with warnings.catch_warnings():
            # Too many pixels is refused just below, with a clearer message.
            warnings.simplefilter("ignore", Pil.DecompressionBombWarning)
            im = Pil.open(io.BytesIO(raw))
        with im:
            width, height = im.size
            if width * height > MAX_PIXELS:
                raise ImageError(f"{name or 'The image'} is too large ({width}×{height} pixels).")
            if max(width, height) <= MAX_SIDE and len(raw) <= MAX_BYTES:
                im.verify()  # a broken file fails here, not at the gateway
                return Image(media_type, base64.b64encode(raw).decode("ascii"), name, width, height)
            im.seek(0)  # an animated GIF: its first frame
            frame = im.convert("RGBA" if _has_alpha(im) else "RGB")
    except ImageError:
        raise
    except Exception as e:  # Pillow raises many kinds for broken files
        raise ImageError(f"{name or 'The image'} couldn't be read ({e}).") from e
    frame.thumbnail((MAX_SIDE, MAX_SIDE))
    out = io.BytesIO()
    if frame.mode == "RGBA":
        frame.save(out, "PNG", optimize=True)
        media_type = "image/png"
    else:
        frame.save(out, "JPEG", quality=85)
        media_type = "image/jpeg"
    data = out.getvalue()
    if len(data) > MAX_BYTES:
        raise ImageError(f"{name or 'The image'} is still larger than {MAX_BYTES // 2**20} MB.")
    return Image(media_type, base64.b64encode(data).decode("ascii"), name, *frame.size)


def _has_alpha(im: object) -> bool:
    mode = getattr(im, "mode", "")
    info = getattr(im, "info", {})
    return mode in ("RGBA", "LA", "PA") or (mode == "P" and "transparency" in info)


def from_base64(data: str, name: str = "") -> Image:
    """An Image from base64 text (a data: URL's payload, or a protocol message)."""
    if data.startswith("data:"):
        data = data.partition(",")[2]
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as e:
        raise ImageError(f"{name or 'The image'} isn't valid base64.") from e
    return prepare(raw, name)


def as_text(image: Image, number: int) -> str:
    """The image for a model that can't see images: its description, if there is one."""
    if image.description:
        by = f' described_by="{image.described_by}"' if image.described_by else ""
        return f'<image n="{number}"{by}>\n{image.description}\n</image>'
    return (
        f'<image n="{number}">(The user attached {image.label(number)}, but this model '
        "can't see images and no description of it was made.)</image>"
    )
