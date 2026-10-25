"""The thumbnail list: multi-selection, drag-reorder and external drops."""
from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QDragEnterEvent, QDragMoveEvent, QDropEvent, QIcon, QPixmap
from PyQt6.QtWidgets import QAbstractItemView, QListView, QListWidget, QListWidgetItem, QWidget

from models.image_item import ImageItem, ItemStatus
from ui.dnd import mime_is_importable, parse_mime
from ui.theme import COLORS
from utils.formatting import format_bytes

_STATUS_TEXT = {
    ItemStatus.READY: "",
    ItemStatus.QUEUED: " · Queued",
    ItemStatus.PROCESSING: " · Processing…",
    ItemStatus.DONE: " · ✔ Saved",
    ItemStatus.FAILED: " · ✖ Failed",
}
_STATUS_COLOR = {
    ItemStatus.PROCESSING: COLORS.accent,
    ItemStatus.DONE: COLORS.success,
    ItemStatus.FAILED: COLORS.danger,
}


class ImageListWidget(QListWidget):
    """Holds ``ImageItem`` objects. Row order == processing order (drag to reorder)."""

    payload_dropped = pyqtSignal(object)  # DropPayload from an external drop
    contents_changed = pyqtSignal()  # items added / removed / reordered

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ImageList")
        self.setViewMode(QListView.ViewMode.ListMode)
        self.setIconSize(QSize(56, 56))
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setUniformItemSizes(True)
        self.setSpacing(2)
        self.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._items: dict[str, ImageItem] = {}
        self._icons: dict[str, QIcon] = {}
        self.model().rowsMoved.connect(lambda *_: self.contents_changed.emit())

    # ------------------------------------------------------------------ #
    # Collection API
    # ------------------------------------------------------------------ #
    def add_item(self, item: ImageItem, row: int | None = None) -> None:
        widget_item = QListWidgetItem()
        widget_item.setData(Qt.ItemDataRole.UserRole, item.id)
        self._items[item.id] = item
        if item.thumb_path is not None:
            self._icons[item.id] = QIcon(QPixmap(str(item.thumb_path)))
        self._apply(widget_item, item)
        if row is None or row >= self.count():
            self.addItem(widget_item)
        else:
            self.insertItem(max(0, row), widget_item)
        self.contents_changed.emit()

    def item_for(self, widget_item: QListWidgetItem | None) -> ImageItem | None:
        if widget_item is None:
            return None
        return self._items.get(widget_item.data(Qt.ItemDataRole.UserRole))

    def get_item(self, item_id: str) -> ImageItem | None:
        return self._items.get(item_id)

    def all_items(self) -> list[ImageItem]:
        return [i for i in (self.item_for(self.item(r)) for r in range(self.count())) if i is not None]

    def selected_items(self) -> list[ImageItem]:
        return [
            i
            for i in (self.item_for(self.item(r)) for r in range(self.count()) if self.item(r).isSelected())
            if i is not None
        ]

    def current_image(self) -> ImageItem | None:
        return self.item_for(self.currentItem())

    def keys(self) -> set[str]:
        """Identity keys of all items (resolved path or URL) for duplicate detection."""
        keys: set[str] = set()
        for item in self._items.values():
            keys.add(item.source_url or _safe_resolve(item.path))
        return keys

    def total_bytes(self) -> int:
        return sum(i.file_size for i in self._items.values())

    def refresh_item(self, item: ImageItem) -> None:
        for row in range(self.count()):
            widget_item = self.item(row)
            if widget_item.data(Qt.ItemDataRole.UserRole) == item.id:
                self._apply(widget_item, item)
                return

    def remove_selected(self) -> list[tuple[int, ImageItem]]:
        rows = sorted((self.row(i) for i in self.selectedItems()))
        return self._remove_rows(rows)

    def remove_ids(self, ids: set[str]) -> list[tuple[int, ImageItem]]:
        rows = [r for r in range(self.count()) if self.item(r).data(Qt.ItemDataRole.UserRole) in ids]
        return self._remove_rows(rows)

    def clear_all(self) -> list[tuple[int, ImageItem]]:
        return self._remove_rows(list(range(self.count())))

    def _remove_rows(self, rows: list[int]) -> list[tuple[int, ImageItem]]:
        removed: list[tuple[int, ImageItem]] = []
        self.blockSignals(True)
        try:
            for row in reversed(rows):
                widget_item = self.takeItem(row)
                item = self._items.pop(widget_item.data(Qt.ItemDataRole.UserRole), None)
                if item is not None:
                    self._icons.pop(item.id, None)
                    removed.append((row, item))
        finally:
            self.blockSignals(False)
        removed.reverse()
        self.itemSelectionChanged.emit()
        self.currentItemChanged.emit(self.currentItem(), None)
        self.contents_changed.emit()
        return removed

    # ------------------------------------------------------------------ #
    def _apply(self, widget_item: QListWidgetItem, item: ImageItem) -> None:
        status = _STATUS_TEXT[item.status]
        widget_item.setText(
            f"{item.display_name}\n{item.width}×{item.height} · {format_bytes(item.file_size)}{status}"
        )
        icon = self._icons.get(item.id)
        if icon is not None:
            widget_item.setIcon(icon)
        tooltip = item.source_url or str(item.path)
        if item.status is ItemStatus.FAILED and item.message:
            tooltip += f"\n\n{item.message}"
        elif item.status is ItemStatus.DONE and item.output_path:
            tooltip += f"\n\nSaved to: {item.output_path}"
        widget_item.setToolTip(tooltip)
        color = _STATUS_COLOR.get(item.status)
        widget_item.setForeground(QBrush(QColor(color or COLORS.text)))
        widget_item.setSizeHint(QSize(0, 68))

    # ------------------------------------------------------------------ #
    # Drag & drop: internal moves reorder, external drops import
    # ------------------------------------------------------------------ #
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.source() is self:
            super().dragEnterEvent(event)
        elif mime_is_importable(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if event.source() is self:
            super().dragMoveEvent(event)
        elif mime_is_importable(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        if event.source() is self:
            super().dropEvent(event)
            return
        payload = parse_mime(event.mimeData())
        if payload.is_empty:
            event.ignore()
            return
        event.acceptProposedAction()
        self.payload_dropped.emit(payload)


def _safe_resolve(path) -> str:  # noqa: ANN001
    try:
        return str(path.resolve())
    except OSError:
        return str(path)
