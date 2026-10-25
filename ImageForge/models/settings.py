"""Persisted application settings."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from models.options import ProcessingOptions


@dataclass
class AppSettings:
    options: ProcessingOptions = field(default_factory=ProcessingOptions)
    window_geometry: str = ""  # base64 of QMainWindow.saveGeometry()
    splitter_sizes: list[int] = field(default_factory=list)
    last_import_dir: str = ""
    last_background_dir: str = ""
    preview_after: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "options": self.options.to_dict(),
            "window_geometry": self.window_geometry,
            "splitter_sizes": self.splitter_sizes,
            "last_import_dir": self.last_import_dir,
            "last_background_dir": self.last_background_dir,
            "preview_after": self.preview_after,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AppSettings:
        options = data.get("options")
        sizes = data.get("splitter_sizes")
        return cls(
            options=ProcessingOptions.from_dict(options if isinstance(options, dict) else {}),
            window_geometry=data.get("window_geometry", "") if isinstance(data.get("window_geometry"), str) else "",
            splitter_sizes=[int(s) for s in sizes if isinstance(s, int)] if isinstance(sizes, list) else [],
            last_import_dir=data.get("last_import_dir", "") if isinstance(data.get("last_import_dir"), str) else "",
            last_background_dir=(
                data.get("last_background_dir", "") if isinstance(data.get("last_background_dir"), str) else ""
            ),
            preview_after=bool(data.get("preview_after", False)),
        )
