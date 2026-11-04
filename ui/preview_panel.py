"""Centre panel: viewer, zoom controls, Before/After toggle and image information."""
from __future__ import annotations

from enum import Enum

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from models.image_item import ImageItem
from ui.image_viewer import ImageViewer
from utils.formatting import format_bytes
from utils.qt_image import LayerPreview


class AfterState(Enum):
    IDLE = "idle"
    PENDING = "pending"
    READY = "ready"
    ERROR = "error"


class PreviewPanel(QWidget):
    after_mode_changed = pyqtSignal(bool)
    payload_dropped = pyqtSignal(object)
    subject_moved = pyqtSignal(float, float)  # manual subject position, fractions of the canvas

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CenterRoot")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)  # QWidget subclasses need this for QSS backgrounds
        self._before: QImage | None = None
        self._after: QImage | LayerPreview | None = None
        self._after_state = AfterState.IDLE
        self._error = ""
        self._before_error = ""
        self._has_item = False
        self._loading_before = False

        # The viewer must exist before the toolbar is built: its buttons connect to it.
        self.viewer = ImageViewer()
        self.viewer.zoom_changed.connect(self._on_zoom_changed)
        self.viewer.payload_dropped.connect(self.payload_dropped)
        self.viewer.subject_moved.connect(self._on_viewer_moved)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_toolbar())
        root.addWidget(self.viewer, 1)
        root.addWidget(self._build_info_bar())
        self._refresh()

    # ------------------------------------------------------------------ #
    def _build_toolbar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("Toolbar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)

        self.before_button = QPushButton("Before")
        self.after_button = QPushButton("After")
        for button, position in ((self.before_button, "left"), (self.after_button, "right")):
            button.setCheckable(True)
            button.setProperty("segment", "true")
            button.setProperty("segmentPos", position)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.before_button.setChecked(True)
        self.before_button.setToolTip("Show the original image (Ctrl+B toggles)")
        self.after_button.setToolTip("Show the processed result (Ctrl+B toggles)")
        group = QButtonGroup(self)
        group.setExclusive(True)
        group.addButton(self.before_button)
        group.addButton(self.after_button)
        self.after_button.toggled.connect(self.after_mode_changed)
        self.after_button.toggled.connect(lambda _: self._refresh())

        layout.addWidget(self.before_button)
        layout.addWidget(self.after_button)
        layout.addStretch(1)

        self.zoom_out_button = self._tool_button("−", "Zoom out (Ctrl+-)")
        self.zoom_out_button.clicked.connect(lambda: self.viewer.zoom_by(1 / 1.25))
        self.zoom_label = QLabel("100%")
        self.zoom_label.setObjectName("Caption")
        self.zoom_label.setMinimumWidth(48)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_in_button = self._tool_button("+", "Zoom in (Ctrl++)")
        self.zoom_in_button.clicked.connect(lambda: self.viewer.zoom_by(1.25))
        self.fit_button = self._tool_button("Fit", "Fit to window (Ctrl+0)")
        self.fit_button.clicked.connect(self.viewer.fit_to_view)
        self.actual_button = self._tool_button("100%", "Actual size (Ctrl+1)")
        self.actual_button.clicked.connect(self.viewer.actual_size)
        for widget in (self.zoom_out_button, self.zoom_label, self.zoom_in_button, self.fit_button, self.actual_button):
            layout.addWidget(widget)
        return bar

    @staticmethod
    def _tool_button(text: str, tooltip: str) -> QPushButton:
        button = QPushButton(text)
        button.setProperty("tool", "true")
        button.setToolTip(tooltip)
        return button

    def _build_info_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("InfoBar")
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(18, 12, 18, 12)
        layout.setSpacing(8)

        grid = QGridLayout()
        grid.setHorizontalSpacing(30)
        grid.setVerticalSpacing(2)
        self._values: dict[str, QLabel] = {}
        for column, name in enumerate(("Width", "Height", "Format", "File Size")):
            caption = QLabel(name.upper())
            caption.setObjectName("Caption")
            value = QLabel("—")
            value.setObjectName("InfoValue")
            grid.addWidget(caption, 0, column)
            grid.addWidget(value, 1, column)
            self._values[name] = value
        grid.setColumnStretch(4, 1)
        layout.addLayout(grid)

        self.output_label = QLabel("")
        self.output_label.setObjectName("Caption")
        layout.addWidget(self.output_label)
        return bar

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    @property
    def after_mode(self) -> bool:
        return self.after_button.isChecked()

    def set_after_mode(self, after: bool) -> None:
        (self.after_button if after else self.before_button).setChecked(True)

    def toggle_mode(self) -> None:
        self.set_after_mode(not self.after_mode)

    def zoom_in(self) -> None:
        self.viewer.zoom_by(1.25)

    def zoom_out(self) -> None:
        self.viewer.zoom_by(1 / 1.25)

    def fit(self) -> None:
        self.viewer.fit_to_view()

    def actual_size(self) -> None:
        self.viewer.actual_size()

    def show_empty(self) -> None:
        self._has_item = False
        self._loading_before = False
        self._before_error = ""
        self._before = self._after = None
        self._after_state = AfterState.IDLE
        self.set_original_info(None)
        self.output_label.setText("")
        self._refresh()

    def begin_item(self, item: ImageItem) -> None:
        """A new image was selected: reset both views and show its metadata."""
        self._has_item = True
        self._loading_before = True
        self._before_error = ""
        self._before = self._after = None
        self._after_state = AfterState.PENDING
        self.set_original_info(item)
        self.output_label.setText("")
        self._refresh()

    def set_before(self, image: QImage) -> None:
        self._before = image
        self._loading_before = False
        self._refresh()

    def set_before_error(self, message: str) -> None:
        self._loading_before = False
        self._before_error = message
        self._error = message
        self._after_state = AfterState.ERROR
        self._refresh()

    def mark_after_pending(self) -> None:
        if self._has_item:
            self._after_state = AfterState.PENDING
            self._refresh()

    def set_after(self, image: QImage | LayerPreview) -> None:
        self._after = image
        self._after_state = AfterState.READY
        self._refresh()

    def set_after_error(self, message: str) -> None:
        self._after = None
        self._error = message
        self._after_state = AfterState.ERROR
        self._refresh()

    def set_original_info(self, item: ImageItem | None) -> None:
        if item is None:
            for label in self._values.values():
                label.setText("—")
            return
        self._values["Width"].setText(f"{item.width} px")
        self._values["Height"].setText(f"{item.height} px")
        self._values["Format"].setText(item.format)
        self._values["File Size"].setText(format_bytes(item.file_size))

    def set_output_summary(self, text: str) -> None:
        self.output_label.setText(text)

    # ------------------------------------------------------------------ #
    def _refresh(self) -> None:
        if not self._has_item:
            self.viewer.set_image(None)
            self.viewer.set_message("Select an image to preview\nor drop files anywhere in this window")
            return
        if not self.after_mode:
            self.viewer.set_image(self._before)
            if self._before_error:
                self.viewer.set_message(self._before_error)
            else:
                self.viewer.set_message("Loading…" if self._loading_before else "")
            return
        # After mode: keep showing the last result while a new one renders (no flicker).
        if self._after is not None and self._after_state in (AfterState.READY, AfterState.PENDING):
            if isinstance(self._after, LayerPreview):
                self.viewer.set_layers(self._after)
            else:
                self.viewer.set_image(self._after)
            self.viewer.set_message("" if self._after_state is AfterState.READY else "Updating preview…")
            return
        self.viewer.set_image(self._before)
        if self._after_state is AfterState.ERROR:
            self.viewer.set_message(f"Preview unavailable: {self._error}")
        else:
            self.viewer.set_message("Rendering preview…")

    def set_subject_offset(self, offset: tuple[float, float]) -> None:
        """Move the subject layer from outside the viewer (reset, undo, apply-to-all)."""
        if isinstance(self._after, LayerPreview):
            self._after.offset = offset
            self._refresh()

    def _on_viewer_moved(self, dx: float, dy: float) -> None:
        # Keep the cached layers in sync, otherwise a refresh would snap the subject back.
        if isinstance(self._after, LayerPreview):
            self._after.offset = (dx, dy)
        self.subject_moved.emit(dx, dy)

    def _on_zoom_changed(self, zoom: float) -> None:
        self.zoom_label.setText(f"{zoom * 100:.0f}%")
