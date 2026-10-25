"""Logging configuration (console + rotating file) and a global exception hook."""
from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable
from logging.handlers import RotatingFileHandler
from types import TracebackType

from utils.paths import log_dir

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if getattr(root, "_imageforge_configured", False):
        return
    root.setLevel(level)
    formatter = logging.Formatter(_FORMAT)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    try:
        file_handler = RotatingFileHandler(
            log_dir() / "imageforge.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError:
        root.warning("Could not open the log file; logging to console only")

    logging.getLogger("PIL").setLevel(logging.WARNING)
    root._imageforge_configured = True  # type: ignore[attr-defined]


def install_exception_hooks(on_error: Callable[[str], None] | None = None) -> None:
    """Log uncaught exceptions (main thread and worker threads) instead of crashing.

    PyQt6 aborts the process when a slot raises and the default ``sys.excepthook`` is
    active, so installing our own hook is essential for a resilient GUI.
    """
    logger = logging.getLogger("imageforge.uncaught")

    def hook(
        exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None
    ) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        logger.critical("Unhandled exception", exc_info=(exc_type, exc, tb))
        if on_error is not None:
            on_error(f"{exc_type.__name__}: {exc}")

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        if args.exc_type is SystemExit or args.exc_value is None:
            return
        hook(args.exc_type, args.exc_value, args.exc_traceback)

    sys.excepthook = hook
    threading.excepthook = thread_hook
