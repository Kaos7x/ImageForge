"""Human-readable formatting helpers."""
from __future__ import annotations


def format_bytes(size: float) -> str:
    """Return ``size`` (bytes) as e.g. ``"1.4 MB"``."""
    value = float(max(size, 0))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024.0 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} GB"  # pragma: no cover - loop always returns


def format_duration(seconds: float) -> str:
    """Return a short duration such as ``"2m 05s"`` or ``"3.4s"``."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, secs = divmod(int(round(seconds)), 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"
