"""Filesystem locations (settings, cache, logs, Downloads) and filename helpers."""
from __future__ import annotations

import logging
import os
import re
import sys
import time
import uuid
from pathlib import Path

from utils.constants import APP_NAME

logger = logging.getLogger(__name__)


def _ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def app_data_dir() -> Path:
    """Directory for persistent per-user data (settings)."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return _ensure(base / APP_NAME)


def cache_dir() -> Path:
    """Directory for disposable data (thumbnails, downloaded images)."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return _ensure(base / APP_NAME)


def log_dir() -> Path:
    return _ensure(cache_dir() / "logs")


def thumbnail_dir() -> Path:
    return _ensure(cache_dir() / "thumbnails")


def download_cache_dir() -> Path:
    return _ensure(cache_dir() / "downloads")


def icon_dir() -> Path:
    return _ensure(cache_dir() / "icons")


# --------------------------------------------------------------------------- #
# Downloads folder detection
# --------------------------------------------------------------------------- #
def _windows_downloads() -> Path | None:
    """Resolve the real Downloads folder (it can be relocated) via the Shell API."""
    try:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        folder_id = uuid.UUID("374DE290-123F-4565-9164-39C4925E467B")  # FOLDERID_Downloads
        guid = GUID(
            folder_id.time_low,
            folder_id.time_mid,
            folder_id.time_hi_version,
            (ctypes.c_ubyte * 8)(*folder_id.bytes[8:]),
        )
        buffer = ctypes.c_wchar_p()
        shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
        ole32 = ctypes.windll.ole32  # type: ignore[attr-defined]
        shell32.SHGetKnownFolderPath.argtypes = [
            ctypes.POINTER(GUID),
            wintypes.DWORD,
            wintypes.HANDLE,
            ctypes.POINTER(ctypes.c_wchar_p),
        ]
        result = shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(buffer))
        try:
            if result == 0 and buffer.value:
                return Path(buffer.value)
        finally:
            ole32.CoTaskMemFree(buffer)
    except Exception:  # noqa: BLE001 - any failure falls back to ~/Downloads
        logger.debug("Windows known-folder lookup failed", exc_info=True)
    return None


def _xdg_downloads() -> Path | None:
    config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "user-dirs.dirs"
    try:
        text = config.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r'^XDG_DOWNLOAD_DIR="?([^"\n]+)"?', text, re.MULTILINE)
    if not match:
        return None
    return Path(match.group(1).replace("$HOME", str(Path.home())))


def get_downloads_dir() -> Path:
    """Return the user's Downloads folder, creating it if necessary."""
    candidate: Path | None = None
    if sys.platform == "win32":
        candidate = _windows_downloads()
    elif sys.platform.startswith("linux"):
        candidate = _xdg_downloads()
    if candidate is None:
        candidate = Path.home() / "Downloads"
    try:
        candidate.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.warning("Cannot create %s; falling back to the home folder", candidate)
        candidate = Path.home()
    return candidate


# --------------------------------------------------------------------------- #
# Filename helpers
# --------------------------------------------------------------------------- #
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_filename(name: str, fallback: str = "image") -> str:
    """Make ``name`` safe to use as a file name on every platform."""
    cleaned = _INVALID_CHARS.sub("_", name).strip(" .")
    return cleaned[:120] or fallback


def prune_old_files(directory: Path, max_age_days: float) -> int:
    """Delete files in ``directory`` older than ``max_age_days``. Returns the count removed."""
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    try:
        for entry in directory.iterdir():
            try:
                if entry.is_file() and entry.stat().st_mtime < cutoff:
                    entry.unlink()
                    removed += 1
            except OSError:
                continue
    except OSError:
        return removed
    return removed
