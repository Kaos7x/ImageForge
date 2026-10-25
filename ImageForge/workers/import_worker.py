"""Imports files, folders and URLs on a background thread."""
from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from services.downloader import DownloadError, ImageDownloader, looks_like_url
from services.image_loader import ImageValidationError, create_item
from services.thumbnail_cache import ThumbnailCache
from utils.constants import IMPORT_EXTENSIONS

logger = logging.getLogger(__name__)


class ImportWorker(QThread):
    """Validates images, builds thumbnails and downloads URLs in parallel.

    Signals
        item_ready(ImageItem)            an image was imported
        item_failed(source, message)     a source could not be imported
        progress(done, total)
        import_finished(ok, failed, cancelled)
    """

    item_ready = pyqtSignal(object)
    item_failed = pyqtSignal(str, str)
    progress = pyqtSignal(int, int)
    import_finished = pyqtSignal(int, int, bool)

    def __init__(
        self,
        sources: list[str],
        existing_keys: set[str],
        thumbnails: ThumbnailCache,
        downloader: ImageDownloader,
        parent=None,  # noqa: ANN001
    ) -> None:
        super().__init__(parent)
        self._sources = sources
        self._existing = set(existing_keys)
        self._thumbnails = thumbnails
        self._downloader = downloader
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    # ------------------------------------------------------------------ #
    def run(self) -> None:  # noqa: D401 - QThread entry point
        ok = failed = 0
        try:
            tasks = self._expand_sources()
            total = len(tasks)
            self.progress.emit(0, total)
            workers = min(8, max(2, (os.cpu_count() or 4)))
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="import") as pool:
                futures = {pool.submit(self._load_one, kind, value): value for kind, value in tasks}
                done = 0
                for future in as_completed(futures):
                    source = futures[future]
                    done += 1
                    try:
                        item = future.result()
                    except (ImageValidationError, DownloadError) as exc:
                        failed += 1
                        self.item_failed.emit(source, str(exc))
                    except Exception as exc:  # noqa: BLE001
                        failed += 1
                        logger.exception("Unexpected import error for %s", source)
                        self.item_failed.emit(source, f"Unexpected error: {exc}")
                    else:
                        if item is not None:
                            ok += 1
                            self.item_ready.emit(item)
                    self.progress.emit(done, total)
                    if self._cancel.is_set():
                        for pending in futures:
                            pending.cancel()
        except Exception:  # noqa: BLE001
            logger.exception("Import failed")
        finally:
            self.import_finished.emit(ok, failed, self._cancel.is_set())

    # ------------------------------------------------------------------ #
    def _expand_sources(self) -> list[tuple[str, str]]:
        """Turn dropped paths/folders/URLs into a flat, de-duplicated task list."""
        tasks: list[tuple[str, str]] = []
        seen: set[str] = set()

        def add(kind: str, value: str, key: str) -> None:
            if key in seen or key in self._existing:
                return
            seen.add(key)
            tasks.append((kind, value))

        for source in self._sources:
            if self._cancel.is_set():
                break
            if looks_like_url(source):
                add("url", source.strip(), source.strip())
                continue
            path = Path(source)
            if path.is_dir():
                found = 0
                for file in self._walk(path):
                    add("file", str(file), self._key(file))
                    found += 1
                if found == 0:
                    self.item_failed.emit(str(path), "No supported images were found in this folder.")
            elif path.exists():
                add("file", str(path), self._key(path))
            else:
                self.item_failed.emit(source, "The file or folder could not be found.")
        return tasks

    @staticmethod
    def _key(path: Path) -> str:
        try:
            return str(path.resolve())
        except OSError:
            return str(path)

    @staticmethod
    def _walk(folder: Path):  # noqa: ANN205
        for root, dirs, files in os.walk(folder):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for name in sorted(files):
                if Path(name).suffix.lower() in IMPORT_EXTENSIONS:
                    yield Path(root) / name

    def _load_one(self, kind: str, value: str):  # noqa: ANN202
        if self._cancel.is_set():
            return None
        if kind == "url":
            local, display = self._downloader.download(value, self._cancel)
            return create_item(local, self._thumbnails, source_url=value, display_name=display)
        return create_item(Path(value), self._thumbnails)
