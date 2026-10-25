"""On-disk thumbnail cache keyed by (path, mtime, size)."""
from __future__ import annotations

import hashlib
import logging
import os
import uuid
from pathlib import Path

from PIL import Image, ImageOps

from utils.constants import THUMBNAIL_SIZE
from utils.image_utils import to_rgba
from utils.paths import thumbnail_dir

logger = logging.getLogger(__name__)

_CACHE_VERSION = "v1"


class ThumbnailCache:
    """Generates small PNG thumbnails once and re-uses them across sessions."""

    def __init__(self, directory: Path | None = None, size: int = THUMBNAIL_SIZE) -> None:
        self._dir = directory or thumbnail_dir()
        self._dir.mkdir(parents=True, exist_ok=True)
        self._size = size

    def _key(self, path: Path) -> str:
        stat = path.stat()
        raw = f"{_CACHE_VERSION}|{path.resolve()}|{stat.st_mtime_ns}|{stat.st_size}|{self._size}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def get(self, path: Path) -> Path:
        """Return the cached thumbnail for ``path``, generating it when missing.

        Raises ``OSError``/``ValueError``/``SyntaxError`` for undecodable images; callers
        translate those into friendly messages.
        """
        target = self._dir / f"{self._key(path)}.png"
        if target.is_file() and target.stat().st_size > 0:
            return target
        self._generate(path, target)
        return target

    def _generate(self, source: Path, target: Path) -> None:
        with Image.open(source) as image:
            # JPEG "draft" mode decodes at reduced size - much faster for big photos.
            image.draft("RGB", (self._size * 2, self._size * 2))
            image.load()
            upright = ImageOps.exif_transpose(image)
            thumb = to_rgba(upright).copy()
        thumb.thumbnail((self._size, self._size), Image.Resampling.LANCZOS)
        temp = target.with_name(f".{uuid.uuid4().hex}.tmp")
        try:
            thumb.save(temp, format="PNG")
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)

    def clear(self) -> int:
        removed = 0
        for entry in self._dir.glob("*.png"):
            try:
                entry.unlink()
                removed += 1
            except OSError:
                continue
        return removed
