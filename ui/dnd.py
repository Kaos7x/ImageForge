"""Drag-and-drop / clipboard payload parsing shared by every drop target.

A drop can carry local files or folders (Explorer, Desktop, Finder), http(s) links
(dragged from a browser or pasted as text), HTML containing ``<img src>`` (dragging an
image out of a web page) or raw image data. ``QMimeData`` is only valid during the drop
event, so everything is extracted synchronously into a ``DropPayload``.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from pathlib import Path

from PyQt6.QtCore import QMimeData
from PyQt6.QtGui import QImage, QPixmap

from services.downloader import looks_like_url

_IMG_SRC = re.compile(r"""<img[^>]+src\s*=\s*["']([^"']+)["']""", re.IGNORECASE)


@dataclass
class DropPayload:
    paths: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    images: list[QImage] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.paths or self.urls or self.images)


def mime_is_importable(mime: QMimeData) -> bool:
    """Cheap check used while dragging (before the drop happens)."""
    if mime.hasUrls() or mime.hasImage() or mime.hasHtml():
        return True
    if mime.hasText():
        lines = mime.text().strip().splitlines()
        return bool(lines) and looks_like_url(lines[0].strip())
    return False


def parse_mime(mime: QMimeData) -> DropPayload:
    payload = DropPayload()

    if mime.hasUrls():
        for url in mime.urls():
            if url.isLocalFile():
                payload.paths.append(url.toLocalFile())
            elif url.scheme().lower() in ("http", "https"):
                payload.urls.append(url.toString())

    if payload.is_empty and mime.hasHtml():
        for match in _IMG_SRC.findall(mime.html()):
            candidate = html.unescape(match)
            if looks_like_url(candidate):
                payload.urls.append(candidate)

    if payload.is_empty and mime.hasText():
        for line in mime.text().splitlines():
            line = line.strip().strip('"')
            if not line:
                continue
            if looks_like_url(line):
                payload.urls.append(line)
            elif Path(line).exists():
                payload.paths.append(line)

    if payload.is_empty and mime.hasImage():
        data = mime.imageData()
        if isinstance(data, QPixmap):
            data = data.toImage()
        if isinstance(data, QImage) and not data.isNull():
            payload.images.append(data)

    # Remove duplicates while keeping order.
    payload.paths = list(dict.fromkeys(payload.paths))
    payload.urls = list(dict.fromkeys(payload.urls))
    return payload
