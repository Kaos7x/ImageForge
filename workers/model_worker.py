"""Background-removal model loading and status broadcasting."""
from __future__ import annotations

import logging

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from models.options import BackgroundQuality
from services.model_manager import ModelLoadError, ModelManager, ModelStatus

logger = logging.getLogger(__name__)


class ModelStatusBridge(QObject):
    """Re-emits ``ModelManager`` status changes as a Qt signal on the GUI thread.

    The manager calls listeners from whichever thread triggered the load; emitting a Qt
    signal makes delivery to widgets thread-safe (queued connection).
    """

    status_changed = pyqtSignal(object)

    def __init__(self, manager: ModelManager, parent: QObject | None = None) -> None:
        super().__init__(parent)
        manager.add_listener(self.status_changed.emit)


class ModelLoadWorker(QThread):
    """Loads (and, on first use, downloads) a model without blocking the UI."""

    def __init__(self, manager: ModelManager, quality: BackgroundQuality, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._manager = manager
        self._quality = quality

    def run(self) -> None:  # noqa: D401 - QThread entry point
        try:
            self._manager.get_session(self._manager.model_for(self._quality))
        except ModelLoadError:
            pass  # the manager already published an ERROR status with the details
        except Exception:  # noqa: BLE001
            logger.exception("Unexpected model-load failure")
