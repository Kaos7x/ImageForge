"""Pillow helpers: safe loading, colour-mode normalisation and orientation handling."""
from __future__ import annotations

import io
import logging
from pathlib import Path

import numpy as np
from PIL import Image, ImageCms, ImageOps

from utils.constants import MAX_SOURCE_PIXELS

logger = logging.getLogger(__name__)

# Pillow refuses (or warns about) images above this size; we validate explicitly as well.
Image.MAX_IMAGE_PIXELS = MAX_SOURCE_PIXELS

_EXIF_ORIENTATION = 0x0112


def oriented_size(image: Image.Image) -> tuple[int, int]:
    """Size of ``image`` after applying its EXIF orientation."""
    width, height = image.size
    try:
        orientation = image.getexif().get(_EXIF_ORIENTATION, 1)
    except Exception:  # noqa: BLE001 - malformed EXIF must never break import
        orientation = 1
    return (height, width) if orientation in (5, 6, 7, 8) else (width, height)


def has_transparency(image: Image.Image) -> bool:
    """True if ``image`` has an alpha channel that is not fully opaque."""
    if image.mode != "RGBA":
        return False
    low, _high = image.getchannel("A").getextrema()
    return low < 255


def to_rgba(image: Image.Image) -> Image.Image:
    """Convert any Pillow image to 8-bit RGBA without losing transparency."""
    mode = image.mode
    if mode == "RGBA":
        return image
    if mode in ("I;16", "I;16L", "I;16B", "I;16N", "I", "F"):
        try:
            array = np.asarray(image, dtype=np.float32)
            peak = float(array.max()) if array.size else 0.0
            if mode.startswith("I;16") or (mode == "I" and 255.0 < peak <= 65535.0):
                array = array / 257.0
            elif mode == "F" and peak <= 1.0:
                array = array * 255.0
            elif peak > 255.0:
                array = array * (255.0 / peak)
            return Image.fromarray(np.clip(array, 0, 255).astype(np.uint8)).convert("RGBA")
        except Exception:  # noqa: BLE001
            logger.debug("High-bit-depth conversion failed; using Pillow's converter", exc_info=True)
    try:
        return image.convert("RGBA")
    except ValueError:
        return image.convert("RGB").convert("RGBA")


def _icc_to_srgb(image: Image.Image) -> Image.Image:
    """Convert an image with an embedded non-sRGB ICC profile to sRGB."""
    icc = image.info.get("icc_profile")
    if not icc or image.mode not in ("RGB", "RGBA"):
        return image
    try:
        source = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        if "srgb" in (ImageCms.getProfileDescription(source) or "").lower():
            return image
        alpha = image.getchannel("A") if image.mode == "RGBA" else None
        converted = ImageCms.profileToProfile(
            image.convert("RGB"), source, ImageCms.createProfile("sRGB"), outputMode="RGB"
        )
        if converted is None:
            return image
        if alpha is not None:
            converted.putalpha(alpha)
        return converted
    except Exception:  # noqa: BLE001 - colour management is best-effort
        logger.debug("ICC conversion failed; keeping original colours", exc_info=True)
        return image


def load_image_rgba(path: Path) -> Image.Image:
    """Fully decode ``path`` into an upright, sRGB, 8-bit RGBA image."""
    with Image.open(path) as opened:
        opened.load()
        image = _icc_to_srgb(opened)
        image = ImageOps.exif_transpose(image)
        return to_rgba(image)


def cap_dimension(image: Image.Image, max_dimension: int) -> Image.Image:
    """Downscale (never upscale) so the longest side is at most ``max_dimension``."""
    longest = max(image.size)
    if longest <= max_dimension:
        return image
    ratio = max_dimension / longest
    size = (max(1, round(image.width * ratio)), max(1, round(image.height * ratio)))
    return image.resize(size, Image.Resampling.LANCZOS)
