"""Distortion-free resize planning.

``plan_layout`` is pure arithmetic: it decides how a source image is scaled and where it is
placed on the output canvas. It is used by the pipeline *and* by the UI (to predict output
file names) so both always agree.
"""
from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

from models.options import FillMode, ProcessingOptions, VerticalAnchor

RGBA = tuple[int, int, int, int]

TRANSPARENT: RGBA = (0, 0, 0, 0)
WHITE: RGBA = (255, 255, 255, 255)
BLACK: RGBA = (0, 0, 0, 255)


@dataclass(frozen=True)
class LayoutPlan:
    scaled_size: tuple[int, int]  # size after proportional scaling of the subject
    canvas_size: tuple[int, int]  # final output size
    crop_box: tuple[int, int, int, int] | None  # crop applied to the scaled subject (COVER)
    paste_pos: tuple[int, int]  # where the (cropped) subject lands on the canvas
    pad_color: RGBA  # canvas fill


def _fit_size(sw: int, sh: int, tw: int, th: int) -> tuple[int, int]:
    """Largest proportional size inside (tw, th); one side equals the target exactly."""
    if tw * sh <= th * sw:
        return tw, max(1, round(sh * tw / sw))
    return max(1, round(sw * th / sh)), th


def _cover_size(sw: int, sh: int, tw: int, th: int) -> tuple[int, int]:
    """Smallest proportional size that fully covers (tw, th)."""
    if tw * sh >= th * sw:
        return tw, max(th, round(sh * tw / sw))
    return max(tw, round(sw * th / sh)), th


def _anchor_offset(canvas: int, content: int, anchor: VerticalAnchor) -> int:
    if anchor is VerticalAnchor.TOP:
        return 0
    if anchor is VerticalAnchor.BOTTOM:
        return canvas - content
    return (canvas - content) // 2


_PAD_COLORS: dict[FillMode, RGBA] = {
    FillMode.FIT: TRANSPARENT,
    FillMode.CONTAIN: TRANSPARENT,
    FillMode.COVER: TRANSPARENT,
    FillMode.PAD_TRANSPARENT: TRANSPARENT,
    FillMode.PAD_WHITE: WHITE,
    FillMode.PAD_BLACK: BLACK,
}


def plan_layout(
    source_size: tuple[int, int], options: ProcessingOptions, background_active: bool
) -> LayoutPlan:
    """Compute scaling, cropping, padding and placement. Never distorts the image."""
    sw, sh = source_size
    if not options.resize_enabled:
        return LayoutPlan((sw, sh), (sw, sh), None, (0, 0), TRANSPARENT)

    tw, th = options.resize_width, options.resize_height
    mode = options.fill_mode
    # The vertical anchor only applies when a background image is being layered.
    anchor = options.vertical_anchor if background_active else VerticalAnchor.CENTER
    pad = _PAD_COLORS[mode]

    if mode is FillMode.COVER:
        w, h = _cover_size(sw, sh, tw, th)
        left = (w - tw) // 2
        if anchor is VerticalAnchor.TOP:
            top = 0  # keep the top edge, crop the bottom
        elif anchor is VerticalAnchor.BOTTOM:
            top = h - th  # keep the bottom edge, crop the top
        else:
            top = (h - th) // 2
        return LayoutPlan((w, h), (tw, th), (left, top, left + tw, top + th), (0, 0), pad)

    w, h = _fit_size(sw, sh, tw, th)
    if mode is FillMode.FIT:
        return LayoutPlan((w, h), (w, h), None, (0, 0), pad)

    # CONTAIN / PAD_*: exact target canvas with the subject centred horizontally.
    return LayoutPlan(
        (w, h), (tw, th), None, ((tw - w) // 2, _anchor_offset(th, h, anchor)), pad
    )


def resize_image(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """High-quality resize. Pillow premultiplies alpha for RGBA, so edges stay clean."""
    if image.size == size:
        return image
    return image.resize(size, Image.Resampling.LANCZOS)
