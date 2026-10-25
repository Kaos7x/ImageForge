"""Right sidebar: every processing option, plus the Process / Cancel footer."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from models.options import (
    PRESET_VALUES,
    BackgroundQuality,
    CompressionPreset,
    FillMode,
    LayerOrder,
    OutputFormat,
    ProcessingOptions,
    VerticalAnchor,
)
from services.image_loader import ImageValidationError, inspect_image
from services.model_manager import ModelState, ModelStatus
from ui.dialogs import show_error
from ui.theme import repolish
from ui.widgets import SectionCard
from utils.constants import MAX_TARGET_DIMENSION
from utils.formatting import format_bytes
from utils.paths import get_downloads_dir
from utils.qt_image import thumbnail_qimage

_QUALITY_HINTS = {
    BackgroundQuality.FAST: "Lightweight U²-Netp model. Quickest, slightly softer edges.",
    BackgroundQuality.BALANCED: "ISNet general-purpose model. Accurate edges for most images.",
    BackgroundQuality.BEST: "ISNet plus alpha matting for the finest hair and edge detail (slower).",
}

_FILL_HINTS = {
    FillMode.FIT: "Scales proportionally so the whole image is visible. The result can be smaller than W×H.",
    FillMode.CONTAIN: "Fits inside W×H and pads to exactly W×H (transparent; white for JPG/BMP).",
    FillMode.COVER: "Fills W×H completely and crops the overflow. Never stretches.",
    FillMode.PAD_TRANSPARENT: "Fits inside W×H with transparent padding (flattened to white for JPG/BMP).",
    FillMode.PAD_WHITE: "Fits inside W×H and pads with white.",
    FillMode.PAD_BLACK: "Fits inside W×H and pads with black.",
}

_COMPRESSION_HINTS = {
    OutputFormat.PNG: "PNG is lossless. Below 70 the palette is reduced to shrink the file.",
    OutputFormat.JPG: "Higher quality = larger file. Transparency is flattened onto white.",
    OutputFormat.JPEG: "Higher quality = larger file. Transparency is flattened onto white.",
    OutputFormat.WEBP: "Lossy WebP quality — or tick Lossless for a perfect copy.",
    OutputFormat.BMP: "BMP is uncompressed; the slider has no effect.",
    OutputFormat.TIFF: "TIFF uses lossless Deflate compression.",
}


def _caption(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Caption")
    return label


def _hint(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setObjectName("Hint")
    label.setWordWrap(True)
    return label


class OptionsPanel(QWidget):
    options_changed = pyqtSignal()
    model_requested = pyqtSignal()
    process_selected_requested = pyqtSignal()
    process_all_requested = pyqtSignal()
    cancel_requested = pyqtSignal()
    open_output_requested = pyqtSignal()
    reset_position_requested = pyqtSignal()
    apply_position_all_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("OptionsRoot")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)  # QWidget subclasses need this for QSS backgrounds
        self.setMinimumWidth(330)
        self.setMaximumWidth(440)

        self._loading = False  # suppress change handling while options are being applied
        self._preset_guard = False
        self._ref_size: tuple[int, int] | None = None
        self._locked_ratio = 1.0
        self._progressive = False
        self._bg_path = ""
        self._output_dir = ""
        self._default_dir = str(get_downloads_dir())
        self.last_background_dir = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QLabel("PROCESSING OPTIONS")
        header.setObjectName("PanelHeader")
        root.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("OptionsContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(14, 2, 14, 14)
        layout.setSpacing(12)
        layout.addWidget(self._build_background_removal())
        layout.addWidget(self._build_resize())
        layout.addWidget(self._build_background_layer())
        layout.addWidget(self._build_compression())
        layout.addWidget(self._build_format())
        layout.addWidget(self._build_output())
        layout.addStretch(1)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)
        root.addWidget(self._build_footer())

        self._update_all_hints()
        self.set_offset_info(None)

    # ================================================================== #
    # Section builders
    # ================================================================== #
    def _build_background_removal(self) -> QWidget:
        card = SectionCard("Background Removal", checkable=True)
        self.bg_remove_card = card
        card.toggled.connect(self._on_bg_remove_toggled)

        card.add_widget(_caption("Quality mode"))
        self.quality_combo = QComboBox()
        for quality in BackgroundQuality:
            self.quality_combo.addItem(quality.label, quality)
        self.quality_combo.setCurrentIndex(1)
        self.quality_combo.currentIndexChanged.connect(self._on_quality_changed)
        card.add_widget(self.quality_combo)
        self.quality_hint = _hint()
        card.add_widget(self.quality_hint)

        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        self.model_dot = QLabel()
        self.model_dot.setObjectName("ModelDot")
        self.model_dot.setProperty("state", "idle")
        self.model_text = QLabel("Model: not loaded")
        self.model_text.setObjectName("Caption")
        self.model_text.setWordWrap(True)
        status_row.addWidget(self.model_dot, 0, Qt.AlignmentFlag.AlignTop)
        status_row.addWidget(self.model_text, 1)
        card.add_layout(status_row)
        return card

    def _build_resize(self) -> QWidget:
        card = SectionCard("Resize", checkable=True)
        self.resize_card = card
        card.toggled.connect(self._emit_changed)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(3)
        self.width_spin = self._dimension_spin(1024)
        self.height_spin = self._dimension_spin(1024)
        multiply = QLabel("×")
        multiply.setAlignment(Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(_caption("Width"), 0, 0)
        grid.addWidget(_caption("Height"), 0, 2)
        grid.addWidget(self.width_spin, 1, 0)
        grid.addWidget(multiply, 1, 1)
        grid.addWidget(self.height_spin, 1, 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(2, 1)
        card.add_layout(grid)
        self.width_spin.valueChanged.connect(self._on_width_changed)
        self.height_spin.valueChanged.connect(self._on_height_changed)

        lock_row = QHBoxLayout()
        self.lock_check = QCheckBox("Lock aspect ratio")
        self.lock_check.setChecked(True)
        self.lock_check.setToolTip("Editing one side updates the other using the selected image's proportions.")
        self.lock_check.toggled.connect(self._on_lock_toggled)
        self.use_size_button = QPushButton("Use image size")
        self.use_size_button.setProperty("variant", "link")
        self.use_size_button.setToolTip("Set width and height to the selected image's size")
        self.use_size_button.clicked.connect(self._use_image_size)
        lock_row.addWidget(self.lock_check)
        lock_row.addStretch(1)
        lock_row.addWidget(self.use_size_button)
        card.add_layout(lock_row)

        card.add_widget(_caption("Fill mode"))
        self.fill_combo = QComboBox()
        for mode in FillMode:
            self.fill_combo.addItem(mode.label, mode)
        self.fill_combo.setCurrentIndex(list(FillMode).index(FillMode.CONTAIN))
        self.fill_combo.currentIndexChanged.connect(self._on_fill_changed)
        card.add_widget(self.fill_combo)
        self.fill_hint = _hint()
        card.add_widget(self.fill_hint)
        return card

    @staticmethod
    def _dimension_spin(value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(1, MAX_TARGET_DIMENSION)
        spin.setValue(value)
        spin.setSuffix(" px")
        spin.setKeyboardTracking(False)  # apply on Enter / focus-out, not every keystroke
        spin.setAccelerated(True)
        return spin

    def _build_background_layer(self) -> QWidget:
        card = SectionCard("Background Layering", checkable=True)
        self.layer_card = card
        card.toggled.connect(self._emit_changed)

        self.bg_preview = QLabel("No background selected")
        self.bg_preview.setObjectName("BgPreview")
        self.bg_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.bg_preview.setFixedHeight(104)
        card.add_widget(self.bg_preview)

        buttons = QHBoxLayout()
        self.select_bg_button = QPushButton("Select Background…")
        self.select_bg_button.clicked.connect(self._choose_background)
        self.clear_bg_button = QPushButton("Clear")
        self.clear_bg_button.clicked.connect(self._clear_background)
        buttons.addWidget(self.select_bg_button, 1)
        buttons.addWidget(self.clear_bg_button)
        card.add_layout(buttons)

        card.add_widget(_caption("Layer order"))
        self.front_radio = QRadioButton("Place subject in front")
        self.behind_radio = QRadioButton("Place subject behind")
        self.behind_radio.setToolTip(
            "The background image is drawn on top of the subject.\n"
            "Use a PNG with transparent areas (frames, overlays) so the subject shows through."
        )
        self.front_radio.setChecked(True)
        order_group = QButtonGroup(self)
        order_group.addButton(self.front_radio)
        order_group.addButton(self.behind_radio)
        order_group.buttonToggled.connect(lambda *_: self._emit_changed())
        card.add_widget(self.front_radio)
        card.add_widget(self.behind_radio)

        card.add_widget(_caption("Subject position"))
        position_row = QHBoxLayout()
        self.center_radio = QRadioButton("Center")
        self.top_radio = QRadioButton("Top")
        self.bottom_radio = QRadioButton("Bottom")
        self.center_radio.setChecked(True)
        position_group = QButtonGroup(self)
        for radio in (self.center_radio, self.top_radio, self.bottom_radio):
            position_group.addButton(radio)
            position_row.addWidget(radio)
        position_group.buttonToggled.connect(lambda *_: self._emit_changed())
        card.add_layout(position_row)

        card.add_widget(_caption("Fine position"))
        self.offset_label = _hint("Not moved")
        card.add_widget(self.offset_label)
        move_row = QHBoxLayout()
        self.reset_position_button = QPushButton("Reset position")
        self.reset_position_button.setToolTip("Put this image's subject back at the position chosen above")
        self.reset_position_button.clicked.connect(self.reset_position_requested)
        self.apply_position_button = QPushButton("Apply to all images")
        self.apply_position_button.setToolTip("Give every image the same manual position as this one")
        self.apply_position_button.clicked.connect(self.apply_position_all_requested)
        move_row.addWidget(self.reset_position_button, 1)
        move_row.addWidget(self.apply_position_button, 1)
        card.add_layout(move_row)
        card.add_widget(
            _hint(
                "Switch to the After view, then drag the subject (or use the arrow keys) to nudge it. "
                "Each image remembers its own position; positions are saved with the output."
            )
        )
        card.add_widget(
            _hint(
                "The background is scaled to cover the output without stretching. "
                "Without Resize, the output keeps the subject's original size."
            )
        )
        return card

    def _build_compression(self) -> QWidget:
        card = SectionCard("Compression")

        card.add_widget(_caption("Preset"))
        self.preset_combo = QComboBox()
        for preset in (
            CompressionPreset.MAX_QUALITY,
            CompressionPreset.BALANCED,
            CompressionPreset.SMALL_FILE,
            CompressionPreset.WEB_OPTIMIZED,
            CompressionPreset.CUSTOM,
        ):
            self.preset_combo.addItem(preset.label, preset)
        self.preset_combo.setCurrentIndex(1)
        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        card.add_widget(self.preset_combo)

        slider_row = QHBoxLayout()
        slider_row.addWidget(_caption("Quality  (0 = smallest · 100 = best)"), 1)
        self.quality_value = QLabel("85")
        self.quality_value.setObjectName("InfoValue")
        slider_row.addWidget(self.quality_value)
        card.add_layout(slider_row)

        self.quality_slider = QSlider(Qt.Orientation.Horizontal)
        self.quality_slider.setRange(0, 100)
        self.quality_slider.setValue(85)
        self.quality_slider.valueChanged.connect(self._on_slider_changed)
        card.add_widget(self.quality_slider)

        self.lossless_check = QCheckBox("WebP lossless")
        self.lossless_check.toggled.connect(self._on_lossless_toggled)
        card.add_widget(self.lossless_check)

        self.compression_hint = _hint()
        card.add_widget(self.compression_hint)
        self.estimate_label = QLabel("Estimated size: —")
        self.estimate_label.setObjectName("EstimateLabel")
        self.estimate_label.setWordWrap(True)
        card.add_widget(self.estimate_label)
        return card

    def _build_format(self) -> QWidget:
        card = SectionCard("Format Conversion")
        self.format_combo = QComboBox()
        for fmt in OutputFormat:
            self.format_combo.addItem(fmt.label, fmt)
        self.format_combo.currentIndexChanged.connect(self._on_format_changed)
        card.add_widget(self.format_combo)
        self.format_note = QLabel("")
        self.format_note.setObjectName("WarnLabel")
        self.format_note.setWordWrap(True)
        card.add_widget(self.format_note)
        return card

    def _build_output(self) -> QWidget:
        card = SectionCard("Output")
        card.add_widget(_caption("Destination folder"))
        self.dir_edit = QLineEdit()
        self.dir_edit.setReadOnly(True)
        self.dir_edit.setText(self._default_dir)
        self.dir_edit.setCursorPosition(0)
        card.add_widget(self.dir_edit)

        row = QHBoxLayout()
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._choose_output_dir)
        reset = QPushButton("Downloads")
        reset.setToolTip("Reset to your Downloads folder")
        reset.clicked.connect(self._reset_output_dir)
        row.addWidget(browse, 1)
        row.addWidget(reset, 1)
        card.add_layout(row)

        self.example_label = QLabel("")
        self.example_label.setObjectName("ExampleLabel")
        self.example_label.setWordWrap(True)
        card.add_widget(self.example_label)

        workers_row = QHBoxLayout()
        workers_row.addWidget(QLabel("Parallel workers"))
        self.workers_spin = QSpinBox()
        self.workers_spin.setRange(1, 16)
        self.workers_spin.setToolTip("How many images are processed at the same time")
        self.workers_spin.valueChanged.connect(self._emit_changed)
        workers_row.addStretch(1)
        workers_row.addWidget(self.workers_spin)
        card.add_layout(workers_row)
        return card

    def _build_footer(self) -> QWidget:
        footer = QFrame()
        footer.setObjectName("Toolbar")
        layout = QVBoxLayout(footer)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        buttons = QHBoxLayout()
        self.process_selected_button = QPushButton("Process Selected")
        self.process_selected_button.setProperty("variant", "primary")
        self.process_selected_button.setToolTip("Process the selected images (Ctrl+Enter)")
        self.process_selected_button.clicked.connect(self.process_selected_requested)
        self.process_all_button = QPushButton("Process All")
        self.process_all_button.setToolTip("Process every image (Ctrl+Shift+Enter)")
        self.process_all_button.clicked.connect(self.process_all_requested)
        buttons.addWidget(self.process_selected_button, 3)
        buttons.addWidget(self.process_all_button, 2)
        layout.addLayout(buttons)

        self.progress_widget = QWidget()
        progress_layout = QVBoxLayout(self.progress_widget)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.setSpacing(4)
        bar_row = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setFormat("%v / %m")
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setProperty("variant", "danger")
        self.cancel_button.setToolTip("Stop processing (Esc)")
        self.cancel_button.clicked.connect(self.cancel_requested)
        bar_row.addWidget(self.progress_bar, 1)
        bar_row.addWidget(self.cancel_button)
        progress_layout.addLayout(bar_row)
        self.progress_label = _caption("")
        progress_layout.addWidget(self.progress_label)
        self.progress_widget.hide()
        layout.addWidget(self.progress_widget)

        bottom = QHBoxLayout()
        self.queue_label = _caption("")
        open_button = QPushButton("Open output folder")
        open_button.setProperty("variant", "link")
        open_button.clicked.connect(self.open_output_requested)
        bottom.addWidget(self.queue_label, 1)
        bottom.addWidget(open_button)
        layout.addLayout(bottom)
        return footer

    # ================================================================== #
    # Public API
    # ================================================================== #
    def get_options(self) -> ProcessingOptions:
        return ProcessingOptions(
            remove_background=self.bg_remove_card.is_checked(),
            background_quality=self.quality_combo.currentData(),
            resize_enabled=self.resize_card.is_checked(),
            resize_width=self.width_spin.value(),
            resize_height=self.height_spin.value(),
            lock_aspect=self.lock_check.isChecked(),
            fill_mode=self.fill_combo.currentData(),
            background_enabled=self.layer_card.is_checked(),
            background_path=self._bg_path,
            layer_order=LayerOrder.FRONT if self.front_radio.isChecked() else LayerOrder.BEHIND,
            vertical_anchor=(
                VerticalAnchor.TOP
                if self.top_radio.isChecked()
                else VerticalAnchor.BOTTOM
                if self.bottom_radio.isChecked()
                else VerticalAnchor.CENTER
            ),
            compression_preset=self.preset_combo.currentData(),
            quality=self.quality_slider.value(),
            webp_lossless=self.lossless_check.isChecked(),
            progressive_jpeg=self._progressive,
            output_format=self.format_combo.currentData(),
            output_dir=self._output_dir,
            max_workers=self.workers_spin.value(),
        )

    def set_options(self, options: ProcessingOptions) -> None:
        self._loading = True
        try:
            self.bg_remove_card.block_toggle_signals(True)
            self.bg_remove_card.set_checked(options.remove_background)
            self.bg_remove_card.block_toggle_signals(False)
            self._set_combo(self.quality_combo, options.background_quality)

            self.resize_card.block_toggle_signals(True)
            self.resize_card.set_checked(options.resize_enabled)
            self.resize_card.block_toggle_signals(False)
            self.width_spin.setValue(options.resize_width)
            self.height_spin.setValue(options.resize_height)
            self.lock_check.setChecked(options.lock_aspect)
            self._locked_ratio = options.resize_width / max(1, options.resize_height)
            self._set_combo(self.fill_combo, options.fill_mode)

            self._bg_path = options.background_path if Path(options.background_path).is_file() else ""
            self.layer_card.block_toggle_signals(True)
            self.layer_card.set_checked(options.background_enabled and bool(self._bg_path))
            self.layer_card.block_toggle_signals(False)
            self.front_radio.setChecked(options.layer_order is LayerOrder.FRONT)
            self.behind_radio.setChecked(options.layer_order is LayerOrder.BEHIND)
            self.top_radio.setChecked(options.vertical_anchor is VerticalAnchor.TOP)
            self.bottom_radio.setChecked(options.vertical_anchor is VerticalAnchor.BOTTOM)
            self.center_radio.setChecked(options.vertical_anchor is VerticalAnchor.CENTER)
            self._update_bg_preview()

            self._preset_guard = True
            self._set_combo(self.preset_combo, options.compression_preset)
            self.quality_slider.setValue(options.quality)
            self.lossless_check.setChecked(options.webp_lossless)
            self._preset_guard = False
            self._progressive = options.progressive_jpeg
            self.quality_value.setText(str(options.quality))

            self._set_combo(self.format_combo, options.output_format)
            self._output_dir = options.output_dir
            self._refresh_output_dir_text()
            self.workers_spin.setValue(options.max_workers)
        finally:
            self._loading = False
            self._preset_guard = False
        self._update_all_hints()

    def set_reference_size(self, size: tuple[int, int] | None) -> None:
        """Aspect ratio source for the lock: the image currently previewed."""
        self._ref_size = size

    def set_rembg_available(self, available: bool) -> None:
        self.bg_remove_card.set_toggle_enabled(available)
        if not available:
            self._loading = True
            self.bg_remove_card.set_checked(False)
            self._loading = False
            self.bg_remove_card.setToolTip("Install rembg to enable background removal (see README).")

    def set_model_status(self, status: ModelStatus) -> None:
        texts = {
            ModelState.UNAVAILABLE: "rembg is not installed — run: pip install rembg onnxruntime",
            ModelState.IDLE: "Model not loaded yet (loads when needed)",
            ModelState.LOADING: "Loading model… the first run downloads it, please wait",
            ModelState.READY: f"Model ready · {status.detail or 'CPU'}",
            ModelState.ERROR: f"Model error: {status.detail}",
        }
        self.model_dot.setProperty("state", status.state.value)
        repolish(self.model_dot)
        self.model_text.setText(texts[status.state])
        self.model_text.setToolTip(status.detail if status.state is ModelState.ERROR else "")

    def set_estimate(self, size: int | None, original: int | None = None, pending: bool = False) -> None:
        if pending:
            self.estimate_label.setText("Estimated size: calculating…")
        elif size is None:
            self.estimate_label.setText("Estimated size: —")
        else:
            text = f"Estimated size: ≈ {format_bytes(size)}"
            if original:
                change = (size - original) / original * 100
                text += f"  ({change:+.0f}% vs original)"
            self.estimate_label.setText(text)

    def set_offset_info(self, offset: tuple[float, float] | None) -> None:
        """Show the selected image's manual subject offset (None = no image selected)."""
        moved = offset is not None and (abs(offset[0]) > 0.0005 or abs(offset[1]) > 0.0005)
        if offset is not None and moved:
            self.offset_label.setText(
                f"Moved: x {offset[0] * 100:+.1f}% · y {offset[1] * 100:+.1f}% of the output size"
            )
        else:
            self.offset_label.setText("Not moved — using the position chosen above")
        self.reset_position_button.setEnabled(bool(moved))
        self.apply_position_button.setEnabled(offset is not None)

    def set_output_example(self, text: str) -> None:
        self.example_label.setText(text)

    def set_running(self, running: bool) -> None:
        self.progress_widget.setVisible(running)
        if not running:
            self.progress_label.setText("")

    def set_progress(self, done: int, total: int, text: str = "") -> None:
        self.progress_bar.setMaximum(max(1, total))
        self.progress_bar.setValue(done)
        if text:
            self.progress_label.setText(text)

    def set_queue_count(self, pending_jobs: int) -> None:
        if pending_jobs > 0:
            noun = "job" if pending_jobs == 1 else "jobs"
            self.queue_label.setText(f"{pending_jobs} more {noun} queued")
        else:
            self.queue_label.setText("")

    def set_actions_enabled(self, has_images: bool, has_selection: bool) -> None:
        self.process_all_button.setEnabled(has_images)
        self.process_selected_button.setEnabled(has_selection)

    # ================================================================== #
    # Handlers
    # ================================================================== #
    def _emit_changed(self, *_args) -> None:  # noqa: ANN002
        if not self._loading:
            self.options_changed.emit()

    @staticmethod
    def _set_combo(combo: QComboBox, data) -> None:  # noqa: ANN001
        # Explicit scan: QComboBox.findData() is unreliable for arbitrary Python objects.
        for index in range(combo.count()):
            if combo.itemData(index) == data:
                combo.setCurrentIndex(index)
                return

    # --- background removal ---
    def _on_bg_remove_toggled(self, checked: bool) -> None:
        if self._loading:
            return
        if checked:
            self.model_requested.emit()
        self._emit_changed()

    def _on_quality_changed(self, _index: int) -> None:
        self.quality_hint.setText(_QUALITY_HINTS[self.quality_combo.currentData()])
        if self._loading:
            return
        if self.bg_remove_card.is_checked():
            self.model_requested.emit()
        self._emit_changed()

    # --- resize ---
    def _current_ratio(self) -> float:
        if self._ref_size and self._ref_size[1] > 0:
            return self._ref_size[0] / self._ref_size[1]
        return self._locked_ratio

    def _set_spin_silently(self, spin: QSpinBox, value: int) -> None:
        spin.blockSignals(True)
        spin.setValue(max(1, value))
        spin.blockSignals(False)

    def _on_width_changed(self, value: int) -> None:
        if self._loading:
            return
        if self.lock_check.isChecked():
            self._set_spin_silently(self.height_spin, round(value / self._current_ratio()))
        self._emit_changed()

    def _on_height_changed(self, value: int) -> None:
        if self._loading:
            return
        if self.lock_check.isChecked():
            self._set_spin_silently(self.width_spin, round(value * self._current_ratio()))
        self._emit_changed()

    def _on_lock_toggled(self, checked: bool) -> None:
        if self._loading:
            return
        if checked:
            if self._ref_size:
                self._set_spin_silently(
                    self.height_spin, round(self.width_spin.value() / self._current_ratio())
                )
            else:
                self._locked_ratio = self.width_spin.value() / max(1, self.height_spin.value())
        self._emit_changed()

    def _use_image_size(self) -> None:
        if not self._ref_size:
            return
        self._set_spin_silently(self.width_spin, self._ref_size[0])
        self._set_spin_silently(self.height_spin, self._ref_size[1])
        self._emit_changed()

    def _on_fill_changed(self, _index: int) -> None:
        self.fill_hint.setText(_FILL_HINTS[self.fill_combo.currentData()])
        self._emit_changed()

    # --- background layering ---
    def _choose_background(self) -> None:
        start = self.last_background_dir or str(Path.home())
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Select background image",
            start,
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff);;All files (*)",
        )
        if not filename:
            return
        try:
            inspect_image(Path(filename))
        except ImageValidationError as exc:
            show_error(self, "Cannot use this background", f"{Path(filename).name}\n\n{exc}")
            return
        self._bg_path = filename
        self.last_background_dir = str(Path(filename).parent)
        self._update_bg_preview()
        self.layer_card.set_checked(True)  # emits via toggled when it was off
        self._emit_changed()

    def _clear_background(self) -> None:
        self._bg_path = ""
        self._update_bg_preview()
        self._emit_changed()

    def _update_bg_preview(self) -> None:
        if not self._bg_path:
            self.bg_preview.setPixmap(QPixmap())
            self.bg_preview.setText("No background selected")
            self.bg_preview.setToolTip("")
        else:
            image = thumbnail_qimage(self._bg_path, 300, 100)
            if image is None:
                self.bg_preview.setText("Preview unavailable")
            else:
                self.bg_preview.setPixmap(QPixmap.fromImage(image))
            self.bg_preview.setToolTip(self._bg_path)
        self.clear_bg_button.setEnabled(bool(self._bg_path))

    # --- compression ---
    def _on_preset_changed(self, _index: int) -> None:
        if self._preset_guard:
            return
        preset = self.preset_combo.currentData()
        if preset is not CompressionPreset.CUSTOM:
            values = PRESET_VALUES[preset]
            self._preset_guard = True
            self.quality_slider.setValue(values.quality)
            self.lossless_check.setChecked(values.webp_lossless)
            self._preset_guard = False
            self._progressive = values.progressive
            self.quality_value.setText(str(values.quality))
        self._emit_changed()

    def _mark_custom(self) -> None:
        self._preset_guard = True
        self._set_combo(self.preset_combo, CompressionPreset.CUSTOM)
        self._preset_guard = False
        self._progressive = False

    def _on_slider_changed(self, value: int) -> None:
        self.quality_value.setText(str(value))
        if self._loading or self._preset_guard:
            return
        self._mark_custom()
        self._emit_changed()

    def _on_lossless_toggled(self, _checked: bool) -> None:
        if self._loading or self._preset_guard:
            return
        self._mark_custom()
        self._emit_changed()

    # --- format ---
    def _on_format_changed(self, _index: int) -> None:
        self._update_format_widgets()
        self._emit_changed()

    def _update_format_widgets(self) -> None:
        fmt: OutputFormat = self.format_combo.currentData()
        self.lossless_check.setEnabled(fmt is OutputFormat.WEBP)
        self.compression_hint.setText(_COMPRESSION_HINTS[fmt])
        self.format_note.setText(
            "" if fmt.supports_alpha
            else f"{fmt.label} has no transparency: transparent areas are filled with white."
        )

    # --- output ---
    def _refresh_output_dir_text(self) -> None:
        shown = self._output_dir or self._default_dir
        self.dir_edit.setText(shown)
        self.dir_edit.setCursorPosition(0)
        self.dir_edit.setToolTip(shown if self._output_dir else f"{shown}\n(your Downloads folder)")

    def _choose_output_dir(self) -> None:
        start = self._output_dir or self._default_dir
        chosen = QFileDialog.getExistingDirectory(self, "Choose output folder", start)
        if chosen:
            self._output_dir = chosen
            self._refresh_output_dir_text()
            self._emit_changed()

    def _reset_output_dir(self) -> None:
        self._output_dir = ""
        self._refresh_output_dir_text()
        self._emit_changed()

    def _update_all_hints(self) -> None:
        self.quality_hint.setText(_QUALITY_HINTS[self.quality_combo.currentData()])
        self.fill_hint.setText(_FILL_HINTS[self.fill_combo.currentData()])
        self._update_format_widgets()
        self.clear_bg_button.setEnabled(bool(self._bg_path))
