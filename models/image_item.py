"""The ``ImageItem`` dataclass: one imported image in the queue."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class ItemStatus(Enum):
    READY = "ready"
    QUEUED = "queued"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


@dataclass
class ImageItem:
    path: Path
    display_name: str
    width: int
    height: int
    format: str
    file_size: int
    thumb_path: Path | None = None
    source_url: str | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: ItemStatus = ItemStatus.READY
    message: str = ""
    output_path: Path | None = None
    # Manual subject position when a background is layered: (dx, dy) as a fraction of the
    # output canvas, added to the planned position. Fractions keep it resolution-independent.
    offset: tuple[float, float] = (0.0, 0.0)

    @property
    def stem(self) -> str:
        """File stem used to build output names (the original name, not a cache name)."""
        return Path(self.display_name).stem or "image"

    @property
    def is_remote(self) -> bool:
        return self.source_url is not None
