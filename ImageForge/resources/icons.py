"""Icons drawn at runtime with QPainter (no binary assets to ship).

Qt style sheets need image files for combo-box arrows, spin-box arrows and check marks, so
they are rendered once into the cache directory and referenced by path from the QSS.
"""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QIcon, QLinearGradient, QPainter, QPen, QPixmap, QPolygonF

from utils.paths import icon_dir


def _canvas(size: int) -> tuple[QPixmap, QPainter]:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    return pixmap, painter


def _arrow(path: str, up: bool, color: str) -> None:
    pixmap, painter = _canvas(16)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    if up:
        points = [QPointF(4, 10), QPointF(12, 10), QPointF(8, 5.5)]
    else:
        points = [QPointF(4, 6), QPointF(12, 6), QPointF(8, 10.5)]
    painter.drawPolygon(QPolygonF(points))
    painter.end()
    pixmap.save(path, "PNG")


def _check(path: str, color: str) -> None:
    pixmap, painter = _canvas(16)
    pen = QPen(QColor(color), 2.2)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.drawPolyline(QPolygonF([QPointF(3.8, 8.4), QPointF(6.6, 11.2), QPointF(12.2, 4.8)]))
    painter.end()
    pixmap.save(path, "PNG")


def ensure_icons(arrow_color: str, check_color: str) -> dict[str, str]:
    """Render the QSS icons and return ``{name: posix_path}``."""
    directory = icon_dir()
    paths = {
        "arrow_down": directory / "arrow_down.png",
        "arrow_up": directory / "arrow_up.png",
        "check": directory / "check.png",
    }
    _arrow(str(paths["arrow_down"]), False, arrow_color)
    _arrow(str(paths["arrow_up"]), True, arrow_color)
    _check(str(paths["check"]), check_color)
    return {name: path.as_posix() for name, path in paths.items()}


def _draw_app_icon(size: int) -> QPixmap:
    pixmap, painter = _canvas(size)
    s = float(size)
    gradient = QLinearGradient(0, 0, s, s)
    gradient.setColorAt(0.0, QColor("#ffb347"))
    gradient.setColorAt(1.0, QColor("#ff5a1f"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(gradient))
    painter.drawRoundedRect(QRectF(s * 0.04, s * 0.04, s * 0.92, s * 0.92), s * 0.22, s * 0.22)
    # Two overlapping "layers" suggest compositing / image stacks.
    painter.setBrush(QColor(255, 255, 255, 110))
    painter.drawRoundedRect(QRectF(s * 0.24, s * 0.24, s * 0.40, s * 0.40), s * 0.07, s * 0.07)
    painter.setBrush(QColor(255, 255, 255, 235))
    painter.drawRoundedRect(QRectF(s * 0.38, s * 0.38, s * 0.40, s * 0.40), s * 0.07, s * 0.07)
    painter.end()
    return pixmap


def create_app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(_draw_app_icon(size))
    return icon


def drop_icon(size: int, color: str) -> QPixmap:
    """A simple 'download into tray' glyph for the drop zone."""
    pixmap, painter = _canvas(size)
    s = float(size)
    pen = QPen(QColor(color), max(2.0, s * 0.07))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.drawLine(QPointF(s * 0.5, s * 0.14), QPointF(s * 0.5, s * 0.60))
    painter.drawPolyline(QPolygonF([QPointF(s * 0.32, s * 0.44), QPointF(s * 0.5, s * 0.62), QPointF(s * 0.68, s * 0.44)]))
    painter.drawPolyline(
        QPolygonF([QPointF(s * 0.16, s * 0.62), QPointF(s * 0.16, s * 0.84), QPointF(s * 0.84, s * 0.84), QPointF(s * 0.84, s * 0.62)])
    )
    painter.end()
    return pixmap
