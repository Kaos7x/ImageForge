"""Application-wide constants and limits."""
from __future__ import annotations

APP_NAME = "ImageForge"
APP_VERSION = "1.0.0"
ORG_NAME = "ImageForge"

# File extensions accepted when importing local files.
IMPORT_EXTENSIONS: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".jpe", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
)

# Pillow format names accepted for local files.
ACCEPTED_FORMATS: frozenset[str] = frozenset(
    {"PNG", "JPEG", "MPO", "WEBP", "BMP", "TIFF", "GIF"}
)

# Pillow format name -> file extension used when saving a downloaded image.
# (Links support jpg, jpeg, png and webp.)
URL_FORMATS: dict[str, str] = {
    "JPEG": ".jpg",
    "MPO": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
}

# Safety limits.
MAX_FILE_BYTES = 250 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024
MAX_SOURCE_PIXELS = 150_000_000
MAX_OUTPUT_PIXELS = 100_000_000
MAX_TARGET_DIMENSION = 16384

# UI / pipeline tuning.
THUMBNAIL_SIZE = 160
PREVIEW_MAX_DIMENSION = 4096
PREVIEW_DEBOUNCE_MS = 450
DOWNLOAD_TIMEOUT_SECONDS = 25
MATTING_MAX_PIXELS = 4_000_000  # alpha matting is O(pixels); skip it above this
