"""Result objects produced by the processing pipeline and batch worker."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ProcessResult:
    output_path: Path
    output_size: tuple[int, int]
    bytes_written: int


@dataclass
class BatchSummary:
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    cancelled: int = 0
    elapsed: float = 0.0
    bytes_written: int = 0
    output_dir: Path | None = None
    outputs: list[Path] = field(default_factory=list)
    succeeded_ids: list[str] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    fatal_error: str | None = None

    def merge(self, other: BatchSummary) -> None:
        self.total += other.total
        self.succeeded += other.succeeded
        self.failed += other.failed
        self.cancelled += other.cancelled
        self.elapsed += other.elapsed
        self.bytes_written += other.bytes_written
        self.outputs.extend(other.outputs)
        self.succeeded_ids.extend(other.succeeded_ids)
        self.errors.extend(other.errors)
        if other.output_dir is not None:
            self.output_dir = other.output_dir
        if other.fatal_error and not self.fatal_error:
            self.fatal_error = other.fatal_error
