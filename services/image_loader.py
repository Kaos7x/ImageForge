"""Validation of imported files and creation of ``ImageItem`` objects."""
from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from models.image_item import ImageItem
from services.thumbnail_cache import ThumbnailCache
from utils.constants import (
    ACCEPTED_FORMATS,
    IMPORT_EXTENSIONS,
    MAX_FILE_BYTES,
    MAX_SOURCE_PIXELS,
)
from utils.formatting import format_bytes
from utils.image_utils import oriented_size

logger = logging.getLogger(__name__)


class ImageValidationError(Exception):
    """An imported file cannot be used. The message is safe to show to the user."""


def inspect_image(path: Path) -> tuple[int, int, str, int]:
    """Validate ``path`` and return ``(width, height, format, file_size)``."""
    try:
        stat = path.stat()
    except FileNotFoundError as exc:
        raise ImageValidationError("The file no longer exists.") from exc
    except PermissionError as exc:
        raise ImageValidationError("Permission denied while reading the file.") from exc
    except OSError as exc:
        raise ImageValidationError(f"The file cannot be read ({exc.strerror or exc}).") from exc

    if not path.is_file():
        raise ImageValidationError("This is not a file.")
    if stat.st_size == 0:
        raise ImageValidationError("The file is empty.")
    if stat.st_size > MAX_FILE_BYTES:
        raise ImageValidationError(
            f"The file is too large ({format_bytes(stat.st_size)}). "
            f"The limit is {format_bytes(MAX_FILE_BYTES)}."
        )
    if path.suffix.lower() not in IMPORT_EXTENSIONS:
        raise ImageValidationError(
            f"Unsupported format '{path.suffix or 'unknown'}'. "
            "Supported: PNG, JPG, JPEG, WEBP, BMP, TIFF, GIF."
        )

    try:
        # Pillow requires verify() to be the very first call after open(), so integrity
        # checking and metadata reading use two cheap, header-only opens.
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            image_format = image.format or ""
            width, height = oriented_size(image)
        if image_format not in ACCEPTED_FORMATS:
            raise ImageValidationError(f"Unsupported image format '{image_format or 'unknown'}'.")
        if width * height > MAX_SOURCE_PIXELS:
            raise ImageValidationError(
                f"The image is too large ({width}×{height} px, "
                f"limit {MAX_SOURCE_PIXELS // 1_000_000} megapixels)."
            )
    except ImageValidationError:
        raise
    except UnidentifiedImageError as exc:
        raise ImageValidationError("This file is not a valid image or is corrupted.") from exc
    except Image.DecompressionBombError as exc:
        raise ImageValidationError("The image is too large to open safely.") from exc
    except (OSError, ValueError, SyntaxError, EOFError) as exc:
        raise ImageValidationError(f"The image is corrupted or incomplete ({exc}).") from exc

    return width, height, ("JPEG" if image_format == "MPO" else image_format), stat.st_size


def create_item(
    path: Path,
    thumbnails: ThumbnailCache,
    source_url: str | None = None,
    display_name: str | None = None,
) -> ImageItem:
    """Validate ``path``, build its thumbnail and return a ready-to-queue ``ImageItem``."""
    width, height, image_format, size = inspect_image(path)
    try:
        thumb = thumbnails.get(path)
    except Image.DecompressionBombError as exc:
        raise ImageValidationError("The image is too large to open safely.") from exc
    except (OSError, ValueError, SyntaxError, EOFError, MemoryError) as exc:
        logger.debug("Thumbnail failed for %s", path, exc_info=True)
        raise ImageValidationError(
            "The image is corrupted or truncated and cannot be decoded."
        ) from exc
    return ImageItem(
        path=path,
        display_name=display_name or path.name,
        width=width,
        height=height,
        format=image_format,
        file_size=size,
        thumb_path=thumb,
        source_url=source_url,
    )
