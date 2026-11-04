"""Processing options: enums and the ``ProcessingOptions`` dataclass.

The options object is a plain snapshot of everything the user configured in the right
sidebar. Batch jobs receive their own copy so later UI changes never affect a running job.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, TypeVar

from utils.constants import MAX_OUTPUT_PIXELS, MAX_TARGET_DIMENSION


class LabeledEnum(Enum):
    """Enum whose members carry a stable storage value and a UI label."""

    label: str

    def __new__(cls, value: str, label: str):  # type: ignore[no-untyped-def]
        obj = object.__new__(cls)
        obj._value_ = value
        obj.label = label
        return obj


E = TypeVar("E", bound=Enum)


def _enum(enum_cls: type[E], raw: Any, default: E) -> E:
    try:
        return enum_cls(raw)
    except ValueError:
        return default


def _bool(raw: Any, default: bool) -> bool:
    return raw if isinstance(raw, bool) else default


def _int(raw: Any, default: int, low: int, high: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def _str(raw: Any, default: str) -> str:
    return raw if isinstance(raw, str) else default


class BackgroundQuality(LabeledEnum):
    FAST = ("fast", "Fast")
    BALANCED = ("balanced", "Balanced")
    BEST = ("best", "Best")


class FillMode(LabeledEnum):
    FIT = ("fit", "Fit")
    CONTAIN = ("contain", "Contain")
    COVER = ("cover", "Cover")
    PAD_TRANSPARENT = ("pad_transparent", "Pad Transparent")
    PAD_WHITE = ("pad_white", "Pad White")
    PAD_BLACK = ("pad_black", "Pad Black")


class LayerOrder(LabeledEnum):
    FRONT = ("front", "Place subject in front")
    BEHIND = ("behind", "Place subject behind")


class VerticalAnchor(LabeledEnum):
    CENTER = ("center", "Center")
    TOP = ("top", "Top")
    BOTTOM = ("bottom", "Bottom")


class CompressionPreset(LabeledEnum):
    CUSTOM = ("custom", "Custom")
    MAX_QUALITY = ("max_quality", "Maximum Quality")
    BALANCED = ("balanced", "Balanced")
    SMALL_FILE = ("small_file", "Small File")
    WEB_OPTIMIZED = ("web_optimized", "Web Optimized")


@dataclass(frozen=True)
class PresetValues:
    quality: int
    webp_lossless: bool
    progressive: bool


PRESET_VALUES: dict[CompressionPreset, PresetValues] = {
    CompressionPreset.MAX_QUALITY: PresetValues(100, True, False),
    CompressionPreset.BALANCED: PresetValues(85, False, False),
    CompressionPreset.SMALL_FILE: PresetValues(55, False, False),
    CompressionPreset.WEB_OPTIMIZED: PresetValues(75, False, True),
}


class OutputFormat(Enum):
    """Output container formats."""

    PNG = ("png", "PNG", "png", "PNG", True)
    JPG = ("jpg", "JPG", "jpg", "JPEG", False)
    JPEG = ("jpeg", "JPEG", "jpeg", "JPEG", False)
    WEBP = ("webp", "WEBP", "webp", "WEBP", True)
    BMP = ("bmp", "BMP", "bmp", "BMP", False)
    TIFF = ("tiff", "TIFF", "tiff", "TIFF", True)

    label: str
    extension: str
    pillow_format: str
    supports_alpha: bool

    def __new__(  # type: ignore[no-untyped-def]
        cls, value: str, label: str, extension: str, pillow_format: str, supports_alpha: bool
    ):
        obj = object.__new__(cls)
        obj._value_ = value
        obj.label = label
        obj.extension = extension
        obj.pillow_format = pillow_format
        obj.supports_alpha = supports_alpha
        return obj


def default_worker_count() -> int:
    return max(1, min(4, os.cpu_count() or 2))


@dataclass
class ProcessingOptions:
    # Background removal
    remove_background: bool = False
    background_quality: BackgroundQuality = BackgroundQuality.BALANCED
    # Resize
    resize_enabled: bool = False
    resize_width: int = 1024
    resize_height: int = 1024
    lock_aspect: bool = True
    fill_mode: FillMode = FillMode.CONTAIN
    # Background layering
    background_enabled: bool = False
    background_path: str = ""
    layer_order: LayerOrder = LayerOrder.FRONT
    vertical_anchor: VerticalAnchor = VerticalAnchor.CENTER
    # Compression
    compression_preset: CompressionPreset = CompressionPreset.BALANCED
    quality: int = 85
    webp_lossless: bool = False
    progressive_jpeg: bool = False
    # Output
    output_format: OutputFormat = OutputFormat.PNG
    output_dir: str = ""  # empty => the user's Downloads folder
    max_workers: int = default_worker_count()

    # ------------------------------------------------------------------ #
    def copy(self) -> ProcessingOptions:
        return replace(self)

    def background_active(self) -> bool:
        """True when a background image is enabled *and* the file exists."""
        return bool(
            self.background_enabled
            and self.background_path
            and Path(self.background_path).is_file()
        )

    def validate(self) -> list[str]:
        """Return a list of user-facing problems (empty when the options are usable)."""
        problems: list[str] = []
        if self.resize_enabled:
            w, h = self.resize_width, self.resize_height
            if w < 1 or h < 1:
                problems.append("Resize width and height must be at least 1 pixel.")
            elif w > MAX_TARGET_DIMENSION or h > MAX_TARGET_DIMENSION:
                problems.append(f"Resize dimensions cannot exceed {MAX_TARGET_DIMENSION} px.")
            elif w * h > MAX_OUTPUT_PIXELS:
                problems.append(
                    f"The requested size ({w}×{h}) is larger than the "
                    f"{MAX_OUTPUT_PIXELS // 1_000_000} megapixel limit."
                )
        if self.background_enabled:
            if not self.background_path:
                problems.append("Background layering is enabled but no background image is selected.")
            elif not Path(self.background_path).is_file():
                problems.append(f"The background image no longer exists:\n{self.background_path}")
        return problems

    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict[str, Any]:
        return {
            "remove_background": self.remove_background,
            "background_quality": self.background_quality.value,
            "resize_enabled": self.resize_enabled,
            "resize_width": self.resize_width,
            "resize_height": self.resize_height,
            "lock_aspect": self.lock_aspect,
            "fill_mode": self.fill_mode.value,
            "background_enabled": self.background_enabled,
            "background_path": self.background_path,
            "layer_order": self.layer_order.value,
            "vertical_anchor": self.vertical_anchor.value,
            "compression_preset": self.compression_preset.value,
            "quality": self.quality,
            "webp_lossless": self.webp_lossless,
            "progressive_jpeg": self.progressive_jpeg,
            "output_format": self.output_format.value,
            "output_dir": self.output_dir,
            "max_workers": self.max_workers,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProcessingOptions:
        d = cls()
        g = data.get
        return cls(
            remove_background=_bool(g("remove_background"), d.remove_background),
            background_quality=_enum(BackgroundQuality, g("background_quality"), d.background_quality),
            resize_enabled=_bool(g("resize_enabled"), d.resize_enabled),
            resize_width=_int(g("resize_width"), d.resize_width, 1, MAX_TARGET_DIMENSION),
            resize_height=_int(g("resize_height"), d.resize_height, 1, MAX_TARGET_DIMENSION),
            lock_aspect=_bool(g("lock_aspect"), d.lock_aspect),
            fill_mode=_enum(FillMode, g("fill_mode"), d.fill_mode),
            background_enabled=_bool(g("background_enabled"), d.background_enabled),
            background_path=_str(g("background_path"), d.background_path),
            layer_order=_enum(LayerOrder, g("layer_order"), d.layer_order),
            vertical_anchor=_enum(VerticalAnchor, g("vertical_anchor"), d.vertical_anchor),
            compression_preset=_enum(CompressionPreset, g("compression_preset"), d.compression_preset),
            quality=_int(g("quality"), d.quality, 0, 100),
            webp_lossless=_bool(g("webp_lossless"), d.webp_lossless),
            progressive_jpeg=_bool(g("progressive_jpeg"), d.progressive_jpeg),
            output_format=_enum(OutputFormat, g("output_format"), d.output_format),
            output_dir=_str(g("output_dir"), d.output_dir),
            max_workers=_int(g("max_workers"), d.max_workers, 1, 16),
        )
