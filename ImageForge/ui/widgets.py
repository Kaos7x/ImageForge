"""Small reusable widgets: section cards and the drop zone."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent, QMouseEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from resources.icons import drop_icon
from ui.dnd import mime_is_importable, parse_mime
from ui.theme import COLORS, repolish


class SectionCard(QFrame):
    """A rounded card with a title (optionally a checkbox that enables the section)."""

    toggled = pyqtSignal(bool)

    def __init__(self, title: str, checkable: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SectionCard")
        self._check: QCheckBox | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 14)
        outer.setSpacing(10)

        if checkable:
            self._check = QCheckBox(title)
            self._check.setObjectName("SectionToggle")
            self._check.toggled.connect(self._on_toggled)
            outer.addWidget(self._check)
        else:
            label = QLabel(title)
            label.setObjectName("SectionTitle")
            outer.addWidget(label)

        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(8)
        outer.addWidget(self.body)
        if checkable:
            self.body.setEnabled(False)

    def is_checked(self) -> bool:
        return self._check.isChecked() if self._check else True

    def set_checked(self, checked: bool) -> None:
        if self._check is not None:
            self._check.setChecked(checked)

    def set_toggle_enabled(self, enabled: bool) -> None:
        if self._check is not None:
            self._check.setEnabled(enabled)

    def block_toggle_signals(self, block: bool) -> None:
        if self._check is not None:
            self._check.blockSignals(block)
            if block is False:
                self.body.setEnabled(self._check.isChecked())

    def _on_toggled(self, checked: bool) -> None:
        self.body.setEnabled(checked)
        self.toggled.emit(checked)

    def add_widget(self, widget: QWidget) -> None:
        self.body_layout.addWidget(widget)

    def add_layout(self, layout) -> None:  # noqa: ANN001
        self.body_layout.addLayout(layout)


class DropZone(QFrame):
    """Dashed target that accepts files, folders and image links; click to browse."""

    payload_dropped = pyqtSignal(object)  # DropPayload
    browse_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(110)
        self.setToolTip("Drop images, folders or image links here — or click to browse")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon = QLabel()
        icon.setPixmap(drop_icon(34, COLORS.muted))
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel("Drop images here")
        title.setObjectName("SectionTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle = QLabel("Files · folders · image links")
        subtitle.setObjectName("Caption")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for widget in (icon, title, subtitle):
            widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            layout.addWidget(widget)

    def _set_active(self, active: bool) -> None:
        self.setProperty("dragActive", active)
        repolish(self)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if mime_is_importable(event.mimeData()):
            event.acceptProposedAction()
            self._set_active(True)
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if mime_is_importable(event.mimeData()):
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:  # noqa: ANN001
        self._set_active(False)
        event.accept()

    def dropEvent(self, event: QDropEvent) -> None:
        self._set_active(False)
        payload = parse_mime(event.mimeData())
        if payload.is_empty:
            event.ignore()
            return
        event.acceptProposedAction()
        self.payload_dropped.emit(payload)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.browse_requested.emit()
        super().mousePressEvent(event)
