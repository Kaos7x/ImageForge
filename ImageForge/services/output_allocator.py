"""Collision-free output file naming.

Naming rule:  <original name>_<width>x<height>.<ext>
If the file exists:  ..._1.<ext>, ..._2.<ext>, ...
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

from models.options import OutputFormat
from utils.paths import sanitize_filename


def build_output_name(stem: str, size: tuple[int, int], fmt: OutputFormat, counter: int = 0) -> str:
    base = f"{sanitize_filename(stem)}_{size[0]}x{size[1]}"
    if counter:
        base = f"{base}_{counter}"
    return f"{base}.{fmt.extension}"


class OutputPathAllocator:
    """Reserves unique file names atomically so parallel workers never collide.

    The file is created empty (``O_EXCL``) at reservation time; the encoder then overwrites
    it. If encoding fails the caller removes the placeholder.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def reserve(self, directory: Path, stem: str, size: tuple[int, int], fmt: OutputFormat) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        with self._lock:
            counter = 0
            while True:
                candidate = directory / build_output_name(stem, size, fmt, counter)
                try:
                    descriptor = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                except FileExistsError:
                    counter += 1
                    continue
                os.close(descriptor)
                return candidate
