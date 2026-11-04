"""Pillow -> Qt image conversion (safe to call from worker threads)."""
from __future__ import annotations

from dataclasses import dataclass

from PIL import Image
from PyQt6.QtGui import QImage


def pil_to_qimage(image: Image.Image) -> QImage:
    """Convert a Pillow image to a detached ``QImage`` (RGBA8888)."""
    if image.mode != "RGBA":
        image = image.convert("RGBA")
    data = image.tobytes("raw", "RGBA")
    qimage = QImage(data, image.width, image.height, image.width * 4, QImage.Format.Format_RGBA8888)
    return qimage.copy()  # detach from the Python bytes buffer before it is freed


def thumbnail_qimage(path: str, max_width: int, max_height: int) -> QImage | None:
    """Decode a small preview of ``path`` with Pillow (draft mode keeps big JPEGs fast)."""
    from PIL import ImageOps

    try:
        with Image.open(path) as opened:
            opened.draft("RGB", (max_width * 2, max_height * 2))
            opened.load()
            upright = ImageOps.exif_transpose(opened).convert("RGBA")
        upright.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
        return pil_to_qimage(upright)
    except Exception:  # noqa: BLE001 - a missing preview is not an error
        return None


@dataclass
class LayerPreview:
    """Layers of an "After" preview with a background, so the subject can be moved live.

    All sizes and positions are in preview pixels. ``origin`` is where the subject's top-left
    corner sits on the canvas *before* the manual offset; ``offset`` is the manual move as a
    fraction of the canvas size (the same unit used when saving).
    """

    canvas_size: tuple[int, int]
    pad_rgba: tuple[int, int, int, int]
    backdrop: QImage
    subject: QImage
    origin: tuple[int, int]
    behind: bool
    offset: tuple[float, float]
