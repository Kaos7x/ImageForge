"""JSON-backed settings persistence."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from models.settings import AppSettings
from utils.paths import app_data_dir

logger = logging.getLogger(__name__)


class SettingsService:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or app_data_dir() / "settings.json"

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> AppSettings:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return AppSettings.from_dict(data)
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            logger.warning("Settings file is unreadable; using defaults", exc_info=True)
        return AppSettings()

    def save(self, settings: AppSettings) -> None:
        temp = self._path.with_suffix(".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temp.write_text(json.dumps(settings.to_dict(), indent=2), encoding="utf-8")
            os.replace(temp, self._path)  # atomic: never leaves a half-written file
        except OSError:
            logger.warning("Could not save settings", exc_info=True)
