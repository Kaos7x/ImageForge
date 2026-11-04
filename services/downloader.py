"""Download images from http(s) URLs with validation and friendly errors."""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from utils.constants import DOWNLOAD_TIMEOUT_SECONDS, MAX_DOWNLOAD_BYTES, URL_FORMATS
from utils.formatting import format_bytes
from utils.paths import sanitize_filename

logger = logging.getLogger(__name__)

_HEADERS = {
    # Many CDNs reject urllib's default agent.
    "User-Agent": "Mozilla/5.0 (compatible; ImageForge/1.0; +https://example.invalid)",
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}


class DownloadError(Exception):
    """A download failed. The message is safe to show to the user."""


def looks_like_url(text: str) -> bool:
    parsed = urllib.parse.urlparse(text.strip())
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


class ImageDownloader:
    def __init__(self, target_dir: Path) -> None:
        self._dir = target_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def download(self, url: str, cancel: threading.Event | None = None) -> tuple[Path, str]:
        """Download ``url``; returns ``(local_path, display_name)``."""
        url = url.strip()
        if not looks_like_url(url):
            raise DownloadError("This is not a valid http(s) link.")
        parsed = urllib.parse.urlparse(url)
        temp = self._dir / f".{uuid.uuid4().hex}.part"
        try:
            request = urllib.request.Request(url, headers=_HEADERS)
            with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
                self._check_headers(response)
                total = 0
                with open(temp, "wb") as handle:
                    while True:
                        if cancel is not None and cancel.is_set():
                            raise DownloadError("The download was cancelled.")
                        chunk = response.read(64 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > MAX_DOWNLOAD_BYTES:
                            raise DownloadError(
                                f"The image is larger than the {format_bytes(MAX_DOWNLOAD_BYTES)} limit."
                            )
                        handle.write(chunk)
            if total == 0:
                raise DownloadError("The server returned an empty response.")

            extension = self._validate(temp)
            stem = sanitize_filename(Path(urllib.parse.unquote(parsed.path)).stem, "image")
            digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
            final = self._dir / f"{stem}_{digest}{extension}"
            os.replace(temp, final)
            return final, f"{stem}{extension}"
        except DownloadError:
            raise
        except urllib.error.HTTPError as exc:
            raise DownloadError(f"The server answered with HTTP {exc.code} ({exc.reason}).") from exc
        except urllib.error.URLError as exc:
            raise DownloadError(f"Could not reach the server: {exc.reason}.") from exc
        except TimeoutError as exc:
            raise DownloadError("The server took too long to respond.") from exc
        except OSError as exc:
            raise DownloadError(f"The download failed: {exc}.") from exc
        finally:
            temp.unlink(missing_ok=True)

    @staticmethod
    def _check_headers(response) -> None:  # type: ignore[no-untyped-def]
        content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type and not (
            content_type.startswith("image/")
            or content_type in ("application/octet-stream", "binary/octet-stream")
        ):
            raise DownloadError(f"The link does not point to an image (it returned {content_type}).")
        length = response.headers.get("Content-Length", "")
        if length.isdigit() and int(length) > MAX_DOWNLOAD_BYTES:
            raise DownloadError(
                f"The image is {format_bytes(int(length))}, above the "
                f"{format_bytes(MAX_DOWNLOAD_BYTES)} limit."
            )

    @staticmethod
    def _validate(path: Path) -> str:
        """Confirm the download is a supported image; returns the right file extension."""
        try:
            with Image.open(path) as image:
                image_format = image.format or ""
                if image_format not in URL_FORMATS:
                    raise DownloadError(
                        f"Unsupported image type '{image_format or 'unknown'}'. "
                        "Links support JPG, JPEG, PNG and WEBP."
                    )
                image.verify()
        except DownloadError:
            raise
        except UnidentifiedImageError as exc:
            raise DownloadError("The downloaded file is not a valid image.") from exc
        except (OSError, ValueError, SyntaxError) as exc:
            raise DownloadError(f"The downloaded image is corrupted ({exc}).") from exc
        return URL_FORMATS[image_format]
