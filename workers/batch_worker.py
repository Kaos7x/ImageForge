"""Parallel batch processing on a ThreadPoolExecutor, driven from a QThread."""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from core.compositor import BackgroundProvider
from core.pipeline import ImagePipeline, ProcessingCancelled
from models.image_item import ImageItem
from models.options import ProcessingOptions
from models.results import BatchSummary, ProcessResult
from services.model_manager import ModelLoadError
from services.output_allocator import OutputPathAllocator
from utils.paths import get_downloads_dir

logger = logging.getLogger(__name__)


@dataclass
class BatchJob:
    job_id: int
    items: list[ImageItem]
    options: ProcessingOptions  # a private snapshot
    # Snapshot of each item's manual position at queue time (later drags don't affect the job).
    offsets: dict[str, tuple[float, float]] = field(default_factory=dict)


def resolve_output_dir(options: ProcessingOptions) -> Path:
    return Path(options.output_dir).expanduser() if options.output_dir else get_downloads_dir()


class BatchWorker(QThread):
    """Processes one ``BatchJob`` with ``options.max_workers`` parallel threads.

    Signals (all emitted from the worker thread; Qt queues them to the GUI thread)
        status_message(text)
        item_started(item_id)
        item_finished(item_id, ProcessResult)
        item_failed(item_id, message)
        item_cancelled(item_id)
        progress(done, total)
        batch_finished(BatchSummary)
    """

    status_message = pyqtSignal(str)
    item_started = pyqtSignal(str)
    item_finished = pyqtSignal(str, object)
    item_failed = pyqtSignal(str, str)
    item_cancelled = pyqtSignal(str)
    progress = pyqtSignal(int, int)
    batch_finished = pyqtSignal(object)

    def __init__(self, job: BatchJob, pipeline: ImagePipeline, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self._job = job
        self._pipeline = pipeline
        self._cancel = threading.Event()
        self._futures: list[Future] = []
        self._futures_lock = threading.Lock()

    @property
    def job(self) -> BatchJob:
        return self._job

    def cancel(self) -> None:
        """Request cancellation: pending items are dropped, running ones stop at the next stage."""
        self._cancel.set()
        with self._futures_lock:
            for future in self._futures:
                future.cancel()

    # ------------------------------------------------------------------ #
    def run(self) -> None:  # noqa: D401 - QThread entry point
        started = time.perf_counter()
        options = self._job.options
        items = self._job.items
        summary = BatchSummary(total=len(items))

        try:
            output_dir = resolve_output_dir(options)
            output_dir.mkdir(parents=True, exist_ok=True)
            summary.output_dir = output_dir
            background = self._prepare_shared_resources(options)
        except Exception as exc:  # noqa: BLE001 - nothing can run without these
            logger.exception("Batch setup failed")
            summary.fatal_error = str(exc) or exc.__class__.__name__
            summary.failed = len(items)
            summary.elapsed = time.perf_counter() - started
            for item in items:
                self.item_failed.emit(item.id, summary.fatal_error)
            self.batch_finished.emit(summary)
            return

        allocator = OutputPathAllocator()
        self.status_message.emit(f"Processing {len(items)} image(s) with {options.max_workers} worker(s)…")
        self.progress.emit(0, len(items))

        done = 0
        with ThreadPoolExecutor(max_workers=options.max_workers, thread_name_prefix="forge") as pool:
            futures: dict[Future, ImageItem] = {}
            with self._futures_lock:
                for item in items:
                    future = pool.submit(self._process_one, item, options, background, allocator, output_dir)
                    futures[future] = item
                    self._futures.append(future)
                if self._cancel.is_set():  # cancelled while submitting
                    for future in futures:
                        future.cancel()

            for future in as_completed(futures):
                item = futures[future]
                done += 1
                try:
                    result: ProcessResult = future.result()
                except (ProcessingCancelled, CancelledError):
                    summary.cancelled += 1
                    self.item_cancelled.emit(item.id)
                except ModelLoadError as exc:
                    self._record_failure(summary, item, str(exc))
                except Exception as exc:  # noqa: BLE001 - one bad image must not stop the batch
                    logger.exception("Failed to process %s", item.path)
                    self._record_failure(summary, item, self._friendly(exc))
                else:
                    summary.succeeded += 1
                    summary.bytes_written += result.bytes_written
                    summary.outputs.append(result.output_path)
                    summary.succeeded_ids.append(item.id)
                    self.item_finished.emit(item.id, result)
                self.progress.emit(done, len(items))

        summary.elapsed = time.perf_counter() - started
        self.batch_finished.emit(summary)

    # ------------------------------------------------------------------ #
    def _prepare_shared_resources(self, options: ProcessingOptions) -> BackgroundProvider | None:
        background: BackgroundProvider | None = None
        if options.background_active():
            self.status_message.emit("Loading background image…")
            background = BackgroundProvider(Path(options.background_path))
        if options.remove_background:
            self.status_message.emit("Loading background-removal model…")
            self._pipeline.warm_up(options)
        return background

    def _process_one(
        self,
        item: ImageItem,
        options: ProcessingOptions,
        background: BackgroundProvider | None,
        allocator: OutputPathAllocator,
        output_dir: Path,
    ) -> ProcessResult:
        if self._cancel.is_set():
            raise ProcessingCancelled()
        self.item_started.emit(item.id)
        return self._pipeline.process_to_file(
            item.path,
            item.stem,
            options,
            background,
            allocator,
            output_dir,
            self._cancel,
            offset=self._job.offsets.get(item.id, (0.0, 0.0)),
        )

    def _record_failure(self, summary: BatchSummary, item: ImageItem, message: str) -> None:
        summary.failed += 1
        summary.errors.append((item.display_name, message))
        self.item_failed.emit(item.id, message)

    @staticmethod
    def _friendly(exc: Exception) -> str:
        if isinstance(exc, MemoryError):
            return "Not enough memory to process this image. Try a smaller output size or fewer workers."
        if isinstance(exc, PermissionError):
            return "Permission denied while writing the output file."
        if isinstance(exc, FileNotFoundError):
            return "The source file no longer exists."
        if isinstance(exc, OSError):
            return f"File error: {exc.strerror or exc}"
        return str(exc) or exc.__class__.__name__
