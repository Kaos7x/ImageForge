"""A small undo stack for user operations (import, remove, clear, batch outputs)."""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from PyQt6.QtCore import QObject, pyqtSignal

logger = logging.getLogger(__name__)


@dataclass
class UndoableOperation:
    description: str
    # Performs the undo. Returns True when it was applied (False keeps it on the stack,
    # e.g. when the user declines a confirmation prompt).
    undo: Callable[[], bool]
    # Operations with the same non-empty key pushed within MERGE_WINDOW seconds collapse into
    # one undo step (e.g. a series of arrow-key nudges), keeping the *first* undo.
    merge_key: str = ""
    timestamp: float = field(default_factory=time.monotonic)


class HistoryService(QObject):
    changed = pyqtSignal()

    def __init__(self, limit: int = 25, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._stack: list[UndoableOperation] = []
        self._limit = limit

    MERGE_WINDOW = 2.0

    def push(self, operation: UndoableOperation) -> None:
        top = self._stack[-1] if self._stack else None
        if (
            top is not None
            and operation.merge_key
            and top.merge_key == operation.merge_key
            and operation.timestamp - top.timestamp < self.MERGE_WINDOW
        ):
            top.timestamp = operation.timestamp  # extend the window, keep the original undo
            return
        self._stack.append(operation)
        del self._stack[: -self._limit]
        self.changed.emit()

    @property
    def can_undo(self) -> bool:
        return bool(self._stack)

    @property
    def last_description(self) -> str:
        return self._stack[-1].description if self._stack else ""

    def undo(self) -> str | None:
        """Undo the most recent operation. Returns its description when applied."""
        if not self._stack:
            return None
        operation = self._stack[-1]
        try:
            applied = operation.undo()
        except Exception:  # noqa: BLE001
            logger.exception("Undo failed for: %s", operation.description)
            self._stack.pop()
            self.changed.emit()
            raise
        if not applied:
            return None
        self._stack.pop()
        self.changed.emit()
        return operation.description

    def clear(self) -> None:
        self._stack.clear()
        self.changed.emit()
