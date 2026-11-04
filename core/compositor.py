"""Layer compositing: padding canvas, custom background image and the subject."""
from __future__ import annotations

import threading
from pathlib import Path

from PIL import Image, ImageOps

from core.resize import LayoutPlan
from models.options import LayerOrder
from utils.image_utils import load_image_rgba
from utils.lru_cache import ThreadSafeLRU


class BackgroundProvider:
    """Holds a decoded background image and serves cover-scaled copies per canvas size.

    The background is scaled with "cover" semantics (fill the canvas, centre-crop the
    overflow) so it is never stretched. Instances are safe to share between threads.
    """

    _shared: ThreadSafeLRU[tuple[str, int], BackgroundProvider] = ThreadSafeLRU(2)

    def __init__(self, path: Path) -> None:
        self.path = path
        self._image = load_image_rgba(path)
        self._lock = threading.Lock()
        self._scaled: dict[tuple[int, int], Image.Image] = {}

    @classmethod
    def shared(cls, path: Path) -> BackgroundProvider:
        """Cached provider used by the live preview (re-decoding per render would be slow)."""
        key = (str(path), path.stat().st_mtime_ns)
        provider = cls._shared.get(key)
        if provider is None:
            provider = cls(path)
            cls._shared.put(key, provider)
        return provider

    def scaled(self, size: tuple[int, int]) -> Image.Image:
        with self._lock:
            image = self._scaled.get(size)
            if image is None:
                image = ImageOps.fit(
                    self._image, size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5)
                )
                if len(self._scaled) >= 4:
                    self._scaled.pop(next(iter(self._scaled)))
                self._scaled[size] = image
            return image


def subject_origin(plan: LayoutPlan) -> tuple[int, int]:
    """Top-left of the *uncropped* scaled subject on the canvas (negative for COVER crops)."""
    x, y = plan.paste_pos
    if plan.crop_box is not None:
        x -= plan.crop_box[0]
        y -= plan.crop_box[1]
    return x, y


def _paste_clipped(canvas: Image.Image, layer: Image.Image, position: tuple[int, int]) -> None:
    """Alpha-composite ``layer`` at ``position``, clipping whatever leaves the canvas."""
    x, y = position
    width, height = layer.size
    left, top = max(0, x), max(0, y)
    right, bottom = min(canvas.width, x + width), min(canvas.height, y + height)
    if right <= left or bottom <= top:
        return  # completely outside the canvas
    if (left, top, right, bottom) != (x, y, x + width, y + height):
        layer = layer.crop((left - x, top - y, right - x, bottom - y))
    canvas.alpha_composite(layer, dest=(left, top))


def compose_layers(
    subject: Image.Image,
    plan: LayoutPlan,
    background: BackgroundProvider | None,
    order: LayerOrder,
    offset: tuple[float, float] = (0.0, 0.0),
) -> Image.Image:
    """Build the final RGBA canvas using correct (non-premultiplied) alpha blending.

    Layer stack, bottom to top:
      * subject in front:   pad colour -> background -> subject
      * subject behind:     pad colour -> subject -> background (needs a PNG with holes)

    ``offset`` moves the subject by a fraction of the canvas size (dx, dy) from its planned
    position. It only applies when a background is layered, and the subject may partly leave
    the canvas (the overflow is clipped).
    """
    canvas_w, canvas_h = canvas_size = plan.canvas_size
    origin_x, origin_y = subject_origin(plan)
    if background is None:
        offset = (0.0, 0.0)
    position = (origin_x + round(offset[0] * canvas_w), origin_y + round(offset[1] * canvas_h))

    if background is None and plan.pad_color[3] == 0:  # nothing to composite onto
        if plan.crop_box is not None:
            return subject.crop(plan.crop_box)
        if position == (0, 0) and subject.size == canvas_size:
            return subject

    canvas = Image.new("RGBA", canvas_size, plan.pad_color)
    if background is None:
        _paste_clipped(canvas, subject, position)
        return canvas

    backdrop = background.scaled(canvas_size)
    if order is LayerOrder.FRONT:
        canvas.alpha_composite(backdrop)
        _paste_clipped(canvas, subject, position)
    else:
        _paste_clipped(canvas, subject, position)
        canvas.alpha_composite(backdrop)
    return canvas
