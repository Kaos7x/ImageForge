"""Main window: wires the sidebar, preview, options, workers and services together."""
from __future__ import annotations

import logging
import os
import threading
import uuid
from collections import deque
from pathlib import Path

from PyQt6.QtCore import QByteArray, Qt, QThreadPool, QTimer, QUrl
from PyQt6.QtGui import QAction, QDesktopServices, QDragEnterEvent, QDragMoveEvent, QDropEvent, QImage, QKeySequence
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMainWindow,
    QProgressBar,
    QSplitter,
)

from core.pipeline import ImagePipeline
from core.resize import plan_layout
from models.image_item import ImageItem, ItemStatus
from models.options import ProcessingOptions
from models.results import BatchSummary, ProcessResult
from services.downloader import ImageDownloader, looks_like_url
from services.history_service import HistoryService, UndoableOperation
from services.model_manager import ModelManager, ModelState
from services.output_allocator import build_output_name
from services.settings_service import SettingsService
from services.thumbnail_cache import ThumbnailCache
from ui import dialogs
from ui.dnd import DropPayload, mime_is_importable, parse_mime
from ui.options_panel import OptionsPanel
from ui.preview_panel import PreviewPanel
from ui.sidebar import ImageSidebar
from utils.constants import APP_NAME, IMPORT_EXTENSIONS, PREVIEW_DEBOUNCE_MS
from utils.formatting import format_bytes
from utils.paths import download_cache_dir
from utils.qt_image import LayerPreview
from workers.batch_worker import BatchJob, BatchWorker, resolve_output_dir
from workers.import_worker import ImportWorker
from workers.model_worker import ModelLoadWorker, ModelStatusBridge
from workers.preview_worker import AfterTask, BeforeTask, PreviewSignals

logger = logging.getLogger(__name__)

_IMAGE_FILTER = "Images (" + " ".join(f"*{ext}" for ext in sorted(IMPORT_EXTENSIONS)) + ");;All files (*)"


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(1120, 700)
        self.resize(1440, 880)
        self.setAcceptDrops(True)

        # ---- services -------------------------------------------------
        self._settings_service = SettingsService()
        self._settings = self._settings_service.load()
        self._models = ModelManager()
        self._pipeline = ImagePipeline(self._models)
        self._thumbs = ThumbnailCache()
        self._downloader = ImageDownloader(download_cache_dir())
        self._history = HistoryService(parent=self)

        # ---- state ----------------------------------------------------
        self._current_item: ImageItem | None = None
        self._import_worker: ImportWorker | None = None
        self._import_queue: deque[list[str]] = deque()
        self._import_added: list[ImageItem] = []
        self._import_failures: list[tuple[str, str]] = []
        self._batch_worker: BatchWorker | None = None
        self._job_queue: deque[BatchJob] = deque()
        self._job_counter = 0
        self._batch_summary: BatchSummary | None = None
        self._cancelling = False
        self._model_worker: ModelLoadWorker | None = None
        self._model_pending = False

        self._preview_pool = QThreadPool(self)
        self._preview_pool.setMaxThreadCount(2)
        self._preview_signals = PreviewSignals(self)
        self._before_token = 0
        self._after_token = 0
        self._after_cancel: threading.Event | None = None
        self._after_options: ProcessingOptions | None = None

        self._after_timer = QTimer(self)
        self._after_timer.setSingleShot(True)
        self._after_timer.timeout.connect(self._run_after_preview)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._save_settings)

        # ---- UI -------------------------------------------------------
        self._build_ui()
        self._build_actions()
        self._connect_signals()
        self._restore_settings()
        self._refresh_stats()
        self._update_undo_action()
        self.preview.show_empty()

    # ================================================================== #
    # Construction
    # ================================================================== #
    def _build_ui(self) -> None:
        self.sidebar = ImageSidebar()
        self.preview = PreviewPanel()
        self.options = OptionsPanel()

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.sidebar)
        splitter.addWidget(self.preview)
        splitter.addWidget(self.options)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setChildrenCollapsible(False)
        splitter.setSizes([330, 740, 380])
        self._splitter = splitter
        self.setCentralWidget(splitter)

        status = self.statusBar()
        status.setSizeGripEnabled(False)
        self._import_progress = QProgressBar()
        self._import_progress.setMaximumWidth(200)
        self._import_progress.setFormat("Importing %v / %m")
        self._import_progress.hide()
        self._selection_label = QLabel("")
        status.addPermanentWidget(self._import_progress)
        status.addPermanentWidget(self._selection_label)
        status.showMessage("Ready — drop images anywhere to begin", 8000)

    def _action(self, menu, text, slot, shortcuts=(), tip=""):  # noqa: ANN001, ANN202
        action = QAction(text, self)
        if shortcuts:
            action.setShortcuts([QKeySequence(s) for s in shortcuts])
        if tip:
            action.setStatusTip(tip)
        action.triggered.connect(lambda _checked=False, fn=slot: fn())
        menu.addAction(action)
        return action

    def _build_actions(self) -> None:
        bar = self.menuBar()
        file_menu = bar.addMenu("&File")
        self._action(file_menu, "Import &Images…", self._import_files_dialog, ["Ctrl+O"])
        self._action(file_menu, "Import &Folder…", self._import_folder_dialog, ["Ctrl+Shift+O"])
        self._action(file_menu, "Import from &URL…", self._import_url_dialog, ["Ctrl+U"])
        self._action(file_menu, "&Paste image / link / files", self._paste, ["Ctrl+V"])
        file_menu.addSeparator()
        self._process_selected_action = self._action(
            file_menu, "Process &Selected", lambda: self._process(True), ["Ctrl+Return", "Ctrl+Enter"]
        )
        self._process_all_action = self._action(
            file_menu, "Process &All", lambda: self._process(False), ["Ctrl+Shift+Return", "Ctrl+Shift+Enter"]
        )
        self._cancel_action = self._action(file_menu, "&Cancel processing", self._cancel_processing, ["Esc"])
        self._cancel_action.setEnabled(False)
        file_menu.addSeparator()
        self._action(file_menu, "&Open output folder", self._open_output_folder, ["Ctrl+E"])
        file_menu.addSeparator()
        self._action(file_menu, "&Quit", self.close, ["Ctrl+Q"])

        edit_menu = bar.addMenu("&Edit")
        self._undo_action = self._action(edit_menu, "&Undo", self._undo, ["Ctrl+Z"])
        edit_menu.addSeparator()
        self._action(edit_menu, "Select &all", self._select_all, ["Ctrl+A"])
        self._action(edit_menu, "&Deselect all", self._deselect_all, ["Ctrl+Shift+A"])
        self._action(edit_menu, "&Remove selected", self._remove_selected, ["Delete"])
        self._action(edit_menu, "&Clear all", self._clear_all, ["Ctrl+Shift+Delete"])

        view_menu = bar.addMenu("&View")
        self._action(view_menu, "Toggle &Before / After", self.preview.toggle_mode, ["Ctrl+B"])
        view_menu.addSeparator()
        self._action(view_menu, "Zoom &in", self.preview.zoom_in, [QKeySequence.StandardKey.ZoomIn, "Ctrl+="])
        self._action(view_menu, "Zoom &out", self.preview.zoom_out, [QKeySequence.StandardKey.ZoomOut])
        self._action(view_menu, "&Fit to window", self.preview.fit, ["Ctrl+0"])
        self._action(view_menu, "&Actual size", self.preview.actual_size, ["Ctrl+1"])

        help_menu = bar.addMenu("&Help")
        self._action(help_menu, "&Keyboard shortcuts", lambda: dialogs.show_shortcuts(self), ["F1"])
        self._action(help_menu, f"&About {APP_NAME}", lambda: dialogs.show_about(self))

    def _connect_signals(self) -> None:
        sidebar = self.sidebar
        sidebar.import_files_requested.connect(self._import_files_dialog)
        sidebar.import_url_requested.connect(self._import_url_dialog)
        sidebar.remove_selected_requested.connect(self._remove_selected)
        sidebar.clear_all_requested.connect(self._clear_all)
        sidebar.select_all_requested.connect(self._select_all)
        sidebar.deselect_all_requested.connect(self._deselect_all)
        sidebar.payload_dropped.connect(self._handle_payload)
        sidebar.list.currentItemChanged.connect(self._on_current_changed)
        sidebar.list.itemSelectionChanged.connect(self._refresh_stats)
        sidebar.list.contents_changed.connect(self._refresh_stats)

        self.preview.payload_dropped.connect(self._handle_payload)
        self.preview.after_mode_changed.connect(lambda _: self._save_timer.start(1200))
        self.preview.subject_moved.connect(self._on_subject_moved)

        options = self.options
        options.options_changed.connect(self._on_options_changed)
        options.model_requested.connect(self._ensure_model)
        options.process_selected_requested.connect(lambda: self._process(True))
        options.process_all_requested.connect(lambda: self._process(False))
        options.cancel_requested.connect(self._cancel_processing)
        options.open_output_requested.connect(self._open_output_folder)
        options.reset_position_requested.connect(self._reset_position)
        options.apply_position_all_requested.connect(self._apply_position_to_all)

        signals = self._preview_signals
        signals.before_ready.connect(self._on_before_ready)
        signals.after_ready.connect(self._on_after_ready)
        signals.failed.connect(self._on_preview_failed)

        self._model_bridge = ModelStatusBridge(self._models, self)
        self._model_bridge.status_changed.connect(options.set_model_status)
        self._history.changed.connect(self._update_undo_action)

    def _restore_settings(self) -> None:
        settings = self._settings
        if settings.window_geometry:
            self.restoreGeometry(QByteArray.fromBase64(settings.window_geometry.encode("ascii")))
        if len(settings.splitter_sizes) == 3 and all(size > 0 for size in settings.splitter_sizes):
            self._splitter.setSizes(settings.splitter_sizes)
        self.options.last_background_dir = settings.last_background_dir
        self.options.set_options(settings.options)
        self.options.set_rembg_available(self._models.is_available())
        self.options.set_model_status(self._models.status)
        self.preview.set_after_mode(settings.preview_after)
        if settings.options.remove_background and self._models.is_available():
            QTimer.singleShot(500, self._ensure_model)

    # ================================================================== #
    # Importing
    # ================================================================== #
    def _import_files_dialog(self) -> None:
        start = self._settings.last_import_dir or str(Path.home())
        files, _ = QFileDialog.getOpenFileNames(self, "Import images", start, _IMAGE_FILTER)
        if files:
            self._settings.last_import_dir = str(Path(files[0]).parent)
            self._start_import(files)

    def _import_folder_dialog(self) -> None:
        start = self._settings.last_import_dir or str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "Import a folder of images", start)
        if folder:
            self._settings.last_import_dir = folder
            self._start_import([folder])

    def _import_url_dialog(self) -> None:
        text, accepted = QInputDialog.getMultiLineText(
            self,
            "Import from URL",
            "Paste one or more direct image links (JPG, JPEG, PNG, WEBP), one per line:",
        )
        if not accepted or not text.strip():
            return
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        urls = [line for line in lines if looks_like_url(line)]
        invalid = [line for line in lines if not looks_like_url(line)]
        if invalid:
            dialogs.show_warning(
                self,
                "Some links were skipped",
                "These lines are not valid http(s) links:\n\n" + "\n".join(invalid[:8]),
            )
        if urls:
            self._start_import(urls)

    def _paste(self) -> None:
        payload = parse_mime(QApplication.clipboard().mimeData())
        if payload.is_empty:
            self._status("The clipboard has no images, files or image links.")
            return
        self._handle_payload(payload)

    def _handle_payload(self, payload: DropPayload) -> None:
        sources = [*payload.paths, *payload.urls]
        for image in payload.images:
            saved = self._save_dropped_image(image)
            if saved is not None:
                sources.append(str(saved))
        if sources:
            self._start_import(sources)

    @staticmethod
    def _save_dropped_image(image: QImage) -> Path | None:
        target = download_cache_dir() / f"pasted_{uuid.uuid4().hex[:8]}.png"
        return target if image.save(str(target), "PNG") else None

    def _start_import(self, sources: list[str]) -> None:
        if self._import_worker is not None:
            self._import_queue.append(sources)  # run after the current import
            return
        self._import_added = []
        self._import_failures = []
        worker = ImportWorker(sources, self.sidebar.list.keys(), self._thumbs, self._downloader, self)
        worker.item_ready.connect(self._on_item_imported)
        worker.item_failed.connect(lambda source, message: self._import_failures.append((source, message)))
        worker.progress.connect(self._on_import_progress)
        worker.import_finished.connect(self._on_import_finished)
        worker.finished.connect(self._on_import_thread_done)
        self._import_worker = worker
        self._import_progress.setRange(0, 0)
        self._import_progress.show()
        self._status("Importing…")
        worker.start()

    def _on_item_imported(self, item: ImageItem) -> None:
        self._import_added.append(item)
        self.sidebar.list.add_item(item)
        if self.sidebar.list.currentRow() < 0:
            self.sidebar.list.setCurrentRow(0)

    def _on_import_progress(self, done: int, total: int) -> None:
        self._import_progress.setRange(0, max(1, total))
        self._import_progress.setValue(done)

    def _on_import_finished(self, ok: int, failed: int, cancelled: bool) -> None:
        added_ids = [item.id for item in self._import_added]
        if added_ids:
            self._history.push(
                UndoableOperation(f"Import {len(added_ids)} image(s)", lambda ids=added_ids: self._undo_import(ids))
            )
        if self._import_failures:
            dialogs.show_import_failures(self, list(self._import_failures), ok)
        if cancelled:
            self._status("Import cancelled.")
        else:
            self._status(f"Imported {ok} image(s)" + (f", {failed} skipped." if failed else "."))

    def _on_import_thread_done(self) -> None:
        worker, self._import_worker = self._import_worker, None
        if worker is not None:
            worker.deleteLater()
        self._import_progress.hide()
        if self._import_queue:
            self._start_import(self._import_queue.popleft())

    def _undo_import(self, ids: list[str]) -> bool:
        self.sidebar.list.remove_ids(set(ids))
        return True

    # ================================================================== #
    # Selection, removal and undo
    # ================================================================== #
    def _select_all(self) -> None:
        self.sidebar.list.selectAll()

    def _deselect_all(self) -> None:
        self.sidebar.list.clearSelection()

    def _remove_selected(self) -> None:
        self._record_removal(self.sidebar.list.remove_selected())

    def _clear_all(self) -> None:
        self._record_removal(self.sidebar.list.clear_all())

    def _record_removal(self, removed: list[tuple[int, ImageItem]]) -> None:
        if not removed:
            return
        count = len(removed)
        self._history.push(
            UndoableOperation(f"Remove {count} image(s)", lambda items=removed: self._restore_items(items))
        )
        self._status(f"Removed {count} image(s). Press Ctrl+Z to undo.")

    def _restore_items(self, removed: list[tuple[int, ImageItem]]) -> bool:
        for row, item in sorted(removed, key=lambda pair: pair[0]):
            if item.status in (ItemStatus.QUEUED, ItemStatus.PROCESSING):
                item.status = ItemStatus.READY
            self.sidebar.list.add_item(item, row)
        if self.sidebar.list.currentRow() < 0 and self.sidebar.list.count():
            self.sidebar.list.setCurrentRow(0)
        return True

    def _undo(self) -> None:
        try:
            description = self._history.undo()
        except Exception as exc:  # noqa: BLE001
            dialogs.show_error(self, "Undo failed", str(exc))
            return
        if description:
            self._status(f"Undid: {description}")

    def _update_undo_action(self) -> None:
        self._undo_action.setEnabled(self._history.can_undo)
        self._undo_action.setText(
            f"&Undo: {self._history.last_description}" if self._history.can_undo else "&Undo"
        )

    # ---- manual subject position (background layering) -----------------
    def _record_position_change(
        self, previous: list[tuple[ImageItem, tuple[float, float]]], description: str, merge_key: str = ""
    ) -> None:
        def undo() -> bool:
            for item, offset in previous:
                item.offset = offset
            current = self._current_item
            if current is not None:
                self.options.set_offset_info(current.offset)
                self.preview.set_subject_offset(current.offset)
                self._after_timer.start(120)
            return True

        self._history.push(UndoableOperation(description, undo, merge_key=merge_key))

    def _on_subject_moved(self, dx: float, dy: float) -> None:
        """The user dragged (or nudged) the subject in the After preview."""
        item = self._current_item
        if item is None:
            return
        new = (round(dx, 5), round(dy, 5))
        old = item.offset
        if abs(new[0] - old[0]) < 1e-4 and abs(new[1] - old[1]) < 1e-4:
            return  # a click without movement
        item.offset = new
        self._record_position_change([(item, old)], "Move subject", merge_key=f"move:{item.id}")
        self.options.set_offset_info(new)
        self._after_timer.start(250)  # the layers already show it; refresh the size estimate

    def _reset_position(self) -> None:
        item = self._current_item
        if item is None or item.offset == (0.0, 0.0):
            return
        previous = item.offset
        item.offset = (0.0, 0.0)
        self._record_position_change([(item, previous)], "Reset subject position")
        self.options.set_offset_info(item.offset)
        self.preview.set_subject_offset(item.offset)
        self._after_timer.start(120)

    def _apply_position_to_all(self) -> None:
        source = self._current_item
        if source is None:
            return
        offset = source.offset
        previous = [(i, i.offset) for i in self.sidebar.list.all_items() if i.offset != offset]
        if not previous:
            self._status("Every image already has this position.")
            return
        for item, _old in previous:
            item.offset = offset
        self._record_position_change(previous, f"Apply position to {len(previous)} image(s)")
        self._status(f"Applied this position to {len(previous)} image(s). Press Ctrl+Z to undo.")

    def _refresh_stats(self) -> None:
        image_list = self.sidebar.list
        selected = len(image_list.selectedItems())
        self.sidebar.set_stats(image_list.count(), image_list.total_bytes(), selected)
        self.options.set_actions_enabled(image_list.count() > 0, selected > 0)
        self._process_selected_action.setEnabled(selected > 0)
        self._process_all_action.setEnabled(image_list.count() > 0)
        self._selection_label.setText(f"{selected} selected" if selected else "")

    # ================================================================== #
    # Preview
    # ================================================================== #
    def _on_current_changed(self, current, _previous) -> None:  # noqa: ANN001
        item = self.sidebar.list.item_for(current)
        self._current_item = item
        self._cancel_after_render()
        if item is None:
            self.preview.show_empty()
            self.options.set_reference_size(None)
            self.options.set_estimate(None)
            self.options.set_output_example("")
            self.options.set_offset_info(None)
            return
        self.options.set_reference_size((item.width, item.height))
        self.options.set_offset_info(item.offset)
        self.preview.begin_item(item)
        self._before_token += 1
        self._preview_pool.start(BeforeTask(self._before_token, item.path, self._preview_signals))
        self._update_output_example()
        self.options.set_estimate(None, pending=True)
        self._after_timer.start(80)

    def _on_options_changed(self) -> None:
        self._update_output_example()
        if self._current_item is not None:
            self._cancel_after_render()
            self.preview.mark_after_pending()
            self.options.set_estimate(None, pending=True)
            self._after_timer.start(PREVIEW_DEBOUNCE_MS)
        self._save_timer.start(1200)

    def _cancel_after_render(self) -> None:
        self._after_timer.stop()
        if self._after_cancel is not None:
            self._after_cancel.set()
            self._after_cancel = None
        self._after_token += 1  # any in-flight result is now stale

    def _run_after_preview(self) -> None:
        item = self._current_item
        if item is None:
            return
        options = self.options.get_options()
        problem = self._preview_problem(options)
        if problem:
            self.preview.set_after_error(problem)
            self.options.set_estimate(None)
            return
        self._after_token += 1
        cancel = threading.Event()
        self._after_cancel = cancel
        self._after_options = options
        self.preview.mark_after_pending()
        self._preview_pool.start(
            AfterTask(
                self._after_token, item.path, options, self._pipeline, cancel, self._preview_signals, item.offset
            )
        )

    def _preview_problem(self, options: ProcessingOptions) -> str:
        problems = options.validate()
        if problems:
            return problems[0].splitlines()[0]
        if options.remove_background and not self._models.is_available():
            return "background removal needs the 'rembg' package (see README)."
        return ""

    def _on_before_ready(self, token: int, image: QImage) -> None:
        if token == self._before_token:
            self.preview.set_before(image)

    def _on_after_ready(self, token: int, image: QImage, estimate: int, width: int, height: int) -> None:
        if token != self._after_token or self._current_item is None or self._after_options is None:
            return
        if isinstance(image, LayerPreview):
            image.offset = self._current_item.offset  # the user may have moved it while this rendered
        self.preview.set_after(image)
        fmt = self._after_options.output_format
        self.preview.set_output_summary(
            f"OUTPUT (estimated):  {width} × {height} px · {fmt.label} · ≈ {format_bytes(estimate)}"
        )
        self.options.set_estimate(estimate, self._current_item.file_size)

    def _on_preview_failed(self, token: int, kind: str, message: str) -> None:
        if kind == "before" and token == self._before_token:
            self.preview.set_before_error(message)
        elif kind == "after" and token == self._after_token:
            self.preview.set_after_error(message)
            self.options.set_estimate(None)

    def _update_output_example(self) -> None:
        item = self._current_item
        if item is None:
            self.options.set_output_example("")
            return
        options = self.options.get_options()
        plan = plan_layout((item.width, item.height), options, options.background_active())
        name = build_output_name(item.stem, plan.canvas_size, options.output_format)
        self.options.set_output_example(f"Output name:  {name}")

    # ================================================================== #
    # Background-removal model
    # ================================================================== #
    def _ensure_model(self) -> None:
        options = self.options.get_options()
        if not options.remove_background or not self._models.is_available():
            return
        name = self._models.model_for(options.background_quality)
        if self._models.is_loaded(name):
            self.options.set_model_status(self._models.status_for(name))
            return
        if self._model_worker is not None:
            self._model_pending = True
            return
        worker = ModelLoadWorker(self._models, options.background_quality, self)
        worker.finished.connect(self._on_model_worker_done)
        self._model_worker = worker
        worker.start()

    def _on_model_worker_done(self) -> None:
        worker, self._model_worker = self._model_worker, None
        if worker is not None:
            worker.deleteLater()
        if self._model_pending:
            self._model_pending = False
            self._ensure_model()
        elif self._current_item is not None and self._models.status.state is ModelState.READY:
            self._after_timer.start(80)  # refresh a preview that was waiting for the model

    # ================================================================== #
    # Processing (queue of batch jobs)
    # ================================================================== #
    def _process(self, selected_only: bool) -> None:
        image_list = self.sidebar.list
        items = image_list.selected_items() if selected_only else image_list.all_items()
        if not items:
            dialogs.show_info(
                self,
                "Nothing to process",
                "Select at least one image first." if selected_only else "Import some images first.",
            )
            return
        options = self.options.get_options()
        if not self._validate_for_processing(options):
            return

        fresh = [i for i in items if i.status not in (ItemStatus.QUEUED, ItemStatus.PROCESSING)]
        if not fresh:
            self._status("Those images are already queued or being processed.")
            return
        for item in fresh:
            item.status = ItemStatus.QUEUED
            item.message = ""
            image_list.refresh_item(item)

        self._job_counter += 1
        self._job_queue.append(
            BatchJob(self._job_counter, fresh, options.copy(), {i.id: i.offset for i in fresh})
        )
        self._status(f"Queued {len(fresh)} image(s).")
        self._update_queue_label()
        self._start_next_job()

    def _validate_for_processing(self, options: ProcessingOptions) -> bool:
        problems = options.validate()
        if options.remove_background and not self._models.is_available():
            problems.append(
                "Background removal needs the 'rembg' package.\n"
                "Install it with:  pip install rembg onnxruntime"
            )
        if problems:
            dialogs.show_warning(self, "Check your settings", "\n\n".join(problems))
            return False
        folder = resolve_output_dir(options)
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            dialogs.show_error(self, "Output folder unavailable", f"Cannot create:\n{folder}\n\n{exc.strerror or exc}")
            return False
        if not os.access(folder, os.W_OK):
            dialogs.show_error(self, "Output folder not writable", f"You do not have permission to write to:\n{folder}")
            return False
        return True

    def _start_next_job(self) -> None:
        if self._batch_worker is not None or not self._job_queue:
            self._update_queue_label()
            return
        job = self._job_queue.popleft()
        worker = BatchWorker(job, self._pipeline, self)
        worker.status_message.connect(self._status)
        worker.item_started.connect(self._on_item_started)
        worker.item_finished.connect(self._on_item_finished)
        worker.item_failed.connect(self._on_item_failed)
        worker.item_cancelled.connect(self._on_item_cancelled)
        worker.progress.connect(self._on_batch_progress)
        worker.batch_finished.connect(self._on_batch_finished)
        worker.finished.connect(self._on_batch_thread_done)
        self._batch_worker = worker
        self._cancelling = False
        if self._batch_summary is None:
            self._batch_summary = BatchSummary()
        self._cancel_action.setEnabled(True)
        self.options.set_running(True)
        self.options.set_progress(0, len(job.items), f"Starting {len(job.items)} image(s)…")
        self._update_queue_label()
        worker.start()

    def _cancel_processing(self) -> None:
        if self._batch_worker is None:
            return
        self._cancelling = True
        for job in self._job_queue:  # drop every queued job
            for item in job.items:
                item.status = ItemStatus.READY
                self.sidebar.list.refresh_item(item)
        self._job_queue.clear()
        self._batch_worker.cancel()
        self._cancel_action.setEnabled(False)
        self._status("Cancelling… finishing the images already in progress.")
        self._update_queue_label()

    def _on_item_started(self, item_id: str) -> None:
        item = self.sidebar.list.get_item(item_id)
        if item is not None:
            item.status = ItemStatus.PROCESSING
            self.sidebar.list.refresh_item(item)

    def _on_item_finished(self, item_id: str, result: ProcessResult) -> None:
        item = self.sidebar.list.get_item(item_id)
        if item is not None:
            item.status = ItemStatus.DONE
            item.output_path = result.output_path
            item.message = ""
            self.sidebar.list.refresh_item(item)

    def _on_item_failed(self, item_id: str, message: str) -> None:
        item = self.sidebar.list.get_item(item_id)
        if item is not None:
            item.status = ItemStatus.FAILED
            item.message = message
            self.sidebar.list.refresh_item(item)

    def _on_item_cancelled(self, item_id: str) -> None:
        item = self.sidebar.list.get_item(item_id)
        if item is not None:
            item.status = ItemStatus.READY
            self.sidebar.list.refresh_item(item)

    def _on_batch_progress(self, done: int, total: int) -> None:
        self.options.set_progress(done, total, f"{done} of {total} finished")

    def _on_batch_finished(self, summary: BatchSummary) -> None:
        if self._batch_summary is None:
            self._batch_summary = BatchSummary()
        self._batch_summary.merge(summary)

    def _on_batch_thread_done(self) -> None:
        worker, self._batch_worker = self._batch_worker, None
        if worker is not None:
            worker.deleteLater()
        if self._job_queue and not self._cancelling:
            self._start_next_job()
            return
        self._finish_all_jobs()

    def _finish_all_jobs(self) -> None:
        self._cancel_action.setEnabled(False)
        self.options.set_running(False)
        self._update_queue_label()
        summary, self._batch_summary = self._batch_summary, None
        self._cancelling = False
        if summary is None:
            return
        if summary.outputs:
            outputs, ids = list(summary.outputs), list(summary.succeeded_ids)
            self._history.push(
                UndoableOperation(
                    f"Processing of {len(outputs)} image(s)",
                    lambda: self._undo_outputs(outputs, ids),
                )
            )
        self._status(f"Done: {summary.succeeded} saved, {summary.failed} failed, {summary.cancelled} cancelled.")
        if dialogs.show_batch_summary(self, summary):
            self._open_folder(summary.output_dir)
        self._refresh_stats()

    def _undo_outputs(self, outputs: list[Path], item_ids: list[str]) -> bool:
        existing = [path for path in outputs if path.exists()]
        if existing and not dialogs.confirm(
            self,
            "Undo processing",
            f"This deletes the {len(existing)} file(s) created by the last run from:\n{existing[0].parent}",
            "Delete files",
        ):
            return False
        failures = 0
        for path in existing:
            try:
                path.unlink()
            except OSError:
                failures += 1
        for item_id in item_ids:
            item = self.sidebar.list.get_item(item_id)
            if item is not None and item.status is ItemStatus.DONE:
                item.status = ItemStatus.READY
                item.output_path = None
                self.sidebar.list.refresh_item(item)
        if failures:
            dialogs.show_warning(self, "Undo incomplete", f"{failures} file(s) could not be deleted.")
        return True

    def _update_queue_label(self) -> None:
        self.options.set_queue_count(len(self._job_queue))

    # ================================================================== #
    # Misc
    # ================================================================== #
    def _open_output_folder(self) -> None:
        self._open_folder(resolve_output_dir(self.options.get_options()))

    def _open_folder(self, folder: Path | None) -> None:
        if folder is None:
            return
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
            dialogs.show_warning(self, "Cannot open folder", f"Could not open:\n{folder}")

    def _status(self, message: str) -> None:
        self.statusBar().showMessage(message, 8000)

    def _save_settings(self) -> None:
        settings = self._settings
        settings.options = self.options.get_options()
        settings.window_geometry = self.saveGeometry().toBase64().data().decode("ascii")
        settings.splitter_sizes = self._splitter.sizes()
        settings.last_background_dir = self.options.last_background_dir
        settings.preview_after = self.preview.after_mode
        self._settings_service.save(settings)

    # ---- drag & drop anywhere on the window ---------------------------
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if mime_is_importable(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if mime_is_importable(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        payload = parse_mime(event.mimeData())
        if payload.is_empty:
            event.ignore()
            return
        event.acceptProposedAction()
        self._handle_payload(payload)

    # ---- shutdown -------------------------------------------------------
    def closeEvent(self, event) -> None:  # noqa: ANN001
        if self._batch_worker is not None:
            if not dialogs.confirm(
                self, "Processing in progress", "Images are still being processed. Cancel and quit?", "Cancel and quit"
            ):
                event.ignore()
                return
            self._job_queue.clear()
            self._batch_worker.cancel()
            self._batch_worker.wait(20000)
        if self._import_worker is not None:
            self._import_worker.cancel()
            self._import_worker.wait(10000)
        self._cancel_after_render()
        self._preview_pool.waitForDone(3000)
        if self._model_worker is not None:
            self._model_worker.wait(3000)
        self._save_settings()
        event.accept()
