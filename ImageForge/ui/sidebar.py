"""Left sidebar: drop zone, image list and list actions."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QMenu, QPushButton, QVBoxLayout, QWidget

from ui.image_list import ImageListWidget
from ui.widgets import DropZone
from utils.formatting import format_bytes


class ImageSidebar(QWidget):
    import_files_requested = pyqtSignal()
    import_url_requested = pyqtSignal()
    remove_selected_requested = pyqtSignal()
    clear_all_requested = pyqtSignal()
    select_all_requested = pyqtSignal()
    deselect_all_requested = pyqtSignal()
    payload_dropped = pyqtSignal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)  # QWidget subclasses need this for QSS backgrounds
        self.setMinimumWidth(290)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 0, 14, 14)
        root.setSpacing(10)

        header = QLabel("IMAGES")
        header.setObjectName("PanelHeader")
        header.setContentsMargins(0, 0, 0, 0)
        root.addWidget(header)

        self.drop_zone = DropZone()
        self.drop_zone.payload_dropped.connect(self.payload_dropped)
        self.drop_zone.browse_requested.connect(self.import_files_requested)
        root.addWidget(self.drop_zone)

        stats_row = QHBoxLayout()
        self.stats_label = QLabel("No images")
        self.stats_label.setObjectName("StatsLabel")
        select_all = QPushButton("Select all")
        select_all.setProperty("variant", "link")
        select_all.clicked.connect(self.select_all_requested)
        select_none = QPushButton("None")
        select_none.setProperty("variant", "link")
        select_none.clicked.connect(self.deselect_all_requested)
        stats_row.addWidget(self.stats_label, 1)
        stats_row.addWidget(select_all)
        stats_row.addWidget(select_none)
        root.addLayout(stats_row)

        self.list = ImageListWidget()
        self.list.payload_dropped.connect(self.payload_dropped)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._show_context_menu)
        root.addWidget(self.list, 1)

        grid = QGridLayout()
        grid.setSpacing(8)
        self.import_button = QPushButton("Import Images")
        self.import_button.setProperty("variant", "primary")
        self.import_button.setToolTip("Import images (Ctrl+O)")
        self.import_button.clicked.connect(self.import_files_requested)
        self.url_button = QPushButton("Import URL")
        self.url_button.setToolTip("Download images from links (Ctrl+U)")
        self.url_button.clicked.connect(self.import_url_requested)
        self.remove_button = QPushButton("Remove Selected")
        self.remove_button.setProperty("variant", "danger")
        self.remove_button.setToolTip("Remove the selected images (Delete)")
        self.remove_button.clicked.connect(self.remove_selected_requested)
        self.clear_button = QPushButton("Clear All")
        self.clear_button.setProperty("variant", "danger")
        self.clear_button.setToolTip("Remove every image (Ctrl+Shift+Delete)")
        self.clear_button.clicked.connect(self.clear_all_requested)
        grid.addWidget(self.import_button, 0, 0)
        grid.addWidget(self.url_button, 0, 1)
        grid.addWidget(self.remove_button, 1, 0)
        grid.addWidget(self.clear_button, 1, 1)
        root.addLayout(grid)

    def set_stats(self, count: int, total_bytes: int, selected: int) -> None:
        if count == 0:
            self.stats_label.setText("No images")
        else:
            noun = "image" if count == 1 else "images"
            text = f"{count} {noun} · {format_bytes(total_bytes)}"
            if selected:
                text += f" · {selected} selected"
            self.stats_label.setText(text)
        self.remove_button.setEnabled(selected > 0)
        self.clear_button.setEnabled(count > 0)

    def _show_context_menu(self, position) -> None:  # noqa: ANN001
        menu = QMenu(self)
        remove = QAction("Remove selected", menu)
        remove.setEnabled(bool(self.list.selectedItems()))
        remove.triggered.connect(self.remove_selected_requested)
        select_all = QAction("Select all", menu)
        select_all.triggered.connect(self.select_all_requested)
        deselect = QAction("Deselect all", menu)
        deselect.triggered.connect(self.deselect_all_requested)
        menu.addAction(remove)
        menu.addSeparator()
        menu.addAction(select_all)
        menu.addAction(deselect)
        menu.exec(self.list.viewport().mapToGlobal(position))
