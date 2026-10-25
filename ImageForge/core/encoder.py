"""Saving, compression and format conversion."""
from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Any

from PIL import Image

from core.resize import resize_image
from models.options import OutputFormat, ProcessingOptions
from utils.image_utils import has_transparency

logger = logging.getLogger(__name__)

_ESTIMATE_SAMPLE_PIXELS = 3_000_000


def prepare_for_format(image: Image.Image, fmt: OutputFormat) -> Image.Image:
    """Pick the right colour mode for ``fmt``.

    * fully opaque images are stored as RGB (smaller files, wider compatibility);
    * formats without alpha (JPG/JPEG/BMP) get transparency flattened onto white.
    """
    rgba = image if image.mode == "RGBA" else image.convert("RGBA")
    if not has_transparency(rgba):
        return rgba.convert("RGB")
    if fmt.supports_alpha:
        return rgba
    flat = Image.new("RGB", rgba.size, (255, 255, 255))
    flat.paste(rgba, mask=rgba.getchannel("A"))
    return flat


def _quantize_png(image: Image.Image, quality: int) -> Image.Image:
    """Lossy PNG: reduce to a palette when the quality slider is low."""
    colors = 256 if quality >= 40 else 64
    try:
        if image.mode == "RGBA":
            return image.quantize(colors=colors, method=Image.Quantize.FASTOCTREE)
        return image.quantize(
            colors=colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.FLOYDSTEINBERG
        )
    except Exception:  # noqa: BLE001
        logger.debug("PNG palette quantisation failed; saving full colour", exc_info=True)
        return image


def _save_kwargs(options: ProcessingOptions, pixels: int) -> dict[str, Any]:
    quality = max(0, min(100, options.quality))
    pillow_format = options.output_format.pillow_format
    if pillow_format == "JPEG":
        return {
            "quality": max(1, quality),
            "optimize": True,
            "progressive": options.progressive_jpeg,
            "subsampling": "4:4:4" if quality >= 90 else "4:2:0",
        }
    if pillow_format == "PNG":
        return {"optimize": True, "compress_level": 9 if pixels <= 16_000_000 else 6}
    if pillow_format == "WEBP":
        if options.webp_lossless:
            big = pixels > 8_000_000
            return {"lossless": True, "quality": 75 if big else 100, "method": 4 if big else 6}
        return {
            "quality": quality,
            "method": 4 if pixels > 8_000_000 else 6,
            "alpha_quality": max(quality, 50),
        }
    if pillow_format == "TIFF":
        return {"compression": "tiff_adobe_deflate"}
    return {}


def _encode(image: Image.Image, options: ProcessingOptions, target: Any) -> None:
    fmt = options.output_format
    prepared = prepare_for_format(image, fmt)
    if fmt.pillow_format == "PNG" and options.quality < 70:
        prepared = _quantize_png(prepared, options.quality)
    kwargs = _save_kwargs(options, image.width * image.height)
    try:
        prepared.save(target, format=fmt.pillow_format, **kwargs)
    except (OSError, ValueError, KeyError) as exc:
        if fmt.pillow_format != "TIFF" or "compression" not in kwargs:
            raise
        logger.warning("TIFF compression unavailable (%s); saving uncompressed", exc)
        if hasattr(target, "seek"):
            target.seek(0)
            target.truncate()
        prepared.save(target, format="TIFF")


def encode_to_file(image: Image.Image, options: ProcessingOptions, destination: Path) -> int:
    """Write ``image`` to ``destination``; returns the file size in bytes."""
    _encode(image, options, destination)
    return destination.stat().st_size


def encode_to_bytes(image: Image.Image, options: ProcessingOptions) -> bytes:
    buffer = io.BytesIO()
    _encode(image, options, buffer)
    return buffer.getvalue()


def estimate_encoded_size(image: Image.Image, options: ProcessingOptions) -> int:
    """Estimate the output file size without writing it.

    Small images are encoded exactly. Large ones are encoded at reduced size and the byte
    count is scaled by the pixel ratio, which keeps the live estimate fast.
    """
    pixels = image.width * image.height
    if pixels <= _ESTIMATE_SAMPLE_PIXELS:
        return len(encode_to_bytes(image, options))
    ratio = (_ESTIMATE_SAMPLE_PIXELS / pixels) ** 0.5
    sample = resize_image(image, (max(1, round(image.width * ratio)), max(1, round(image.height * ratio))))
    sample_bytes = len(encode_to_bytes(sample, options))
    return int(sample_bytes * pixels / (sample.width * sample.height))
