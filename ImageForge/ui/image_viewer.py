"""Zoomable, pannable image viewer with a transparency checkerboard.

Two display modes:
* **flat**    - one image (Before view, or an After view without a background);
* **layered** - a canvas that clips a background item and a *movable* subject item, used
  when background layering is on. Dragging the subject (or pressing the arrow keys) moves
  it live and reports the new position as a fraction of the canvas via ``subject_moved``.
"""
from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QPoint, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
    QResizeEvent,
    QWheelEvent,
)
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QLabel,
    QWidget,
)

from ui.dnd import mime_is_importable, parse_mime
from ui.theme import COLORS
from utils.qt_image import LayerPreview

NUDGE_STEP = 0.005  # fraction of the canvas per arrow-key press
NUDGE_STEP_LARGE = 0.02  # with Shift


def _checker_pixmap() -> QPixmap:
    tile = QPixmap(16, 16)
    painter = QPainter(tile)
    painter.fillRect(0, 0, 16, 16, QColor("#2b3038"))
    painter.fillRect(0, 0, 8, 8, QColor("#232830"))
    painter.fillRect(8, 8, 8, 8, QColor("#232830"))
    painter.end()
    return tile


class _SubjectItem(QGraphicsPixmapItem):
    """The movable subject layer. Reports the start and end of a drag to the viewer."""

    def __init__(self, parent: QGraphicsItem | None = None) -> None:
        super().__init__(parent)
        self.on_pressed: Callable[[], None] | None = None
        self.on_released: Callable[[], None] | None = None
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)

    def mousePressEvent(self, event) -> None:  # noqa: ANN001
        if self.on_pressed is not None:
            self.on_pressed()
        # a movable item accepts the press -> the view won't pan
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001
        super().mouseReleaseEvent(event)
        if self.on_released is not None:
            self.on_released()


class ImageViewer(QGraphicsView):
    zoom_changed = pyqtSignal(float)
    payload_dropped = pyqtSignal(object)
    # (dx, dy) as fractions of the canvas size
    subject_moved = pyqtSignal(float, float)

    MIN_ZOOM = 0.02
    MAX_ZOOM = 32.0
    MAX_FIT_ZOOM = 8.0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        # flat mode
        self._item = QGraphicsPixmapItem()
        self._item.setTransformationMode(
            Qt.TransformationMode.SmoothTransformation)
        self._scene.addItem(self._item)

        # layered mode: canvas (clips its children) -> backdrop + movable subject
        self._canvas = QGraphicsRectItem()
        self._canvas.setPen(QPen(Qt.PenStyle.NoPen))
        self._canvas.setFlag(
            QGraphicsItem.GraphicsItemFlag.ItemClipsChildrenToShape, True)
        self._scene.addItem(self._canvas)
        self._backdrop_item = QGraphicsPixmapItem(self._canvas)
        self._subject_item = _SubjectItem(self._canvas)
        self._subject_item.on_pressed = self._on_subject_pressed
        self._subject_item.on_released = self._on_subject_released
        self._canvas.setVisible(False)

        self._layered = False
        self._drag_active = False
        self._origin = (0, 0)
        self._checker = _checker_pixmap()
        self._fit = True
        self._zoom = 1.0

        self.setRenderHints(
            QPainter.RenderHint.SmoothPixmapTransform | QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setTransformationAnchor(
            QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setViewportUpdateMode(
            QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # arrow-key nudging needs keyboard focus
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAcceptDrops(True)

        self._overlay = QLabel(self.viewport())
        self._overlay.setObjectName("ViewerOverlay")
        self._overlay.setWordWrap(True)
        self._overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._overlay.hide()

        self._hint = QLabel(
            "Drag the subject to move it  ·  arrow keys nudge (Shift = larger steps)", self.viewport())
        self._hint.setObjectName("ViewerHint")
        self._hint.hide()

    # ------------------------------------------------------------------ #
    # Content
    # ------------------------------------------------------------------ #
    def _content_item(self) -> QGraphicsItem:
        return self._canvas if self._layered else self._item

    @property
    def has_image(self) -> bool:
        return self._layered or not self._item.pixmap().isNull()

    @property
    def zoom(self) -> float:
        return self._zoom

    @property
    def is_layered(self) -> bool:
        return self._layered

    def set_image(self, image: QImage | None) -> None:
        """Show a single flat image (or clear the view)."""
        self._layered = False
        self._canvas.setVisible(False)
        self._hint.hide()
        self._item.setVisible(True)
        if image is None or image.isNull():
            self._item.setPixmap(QPixmap())
            self._scene.setSceneRect(QRectF())
        else:
            pixmap = QPixmap.fromImage(image)
            self._item.setPixmap(pixmap)
            self._scene.setSceneRect(
                QRectF(0, 0, pixmap.width(), pixmap.height()))
            if self._fit:
                self.fit_to_view()
            else:
                self._update_sampling()
        self._position_overlay()
        self.viewport().update()

    def set_layers(self, layers: LayerPreview) -> None:
        """Show the background and the movable subject as separate layers."""
        if self._drag_active:
            if QApplication.mouseButtons() & Qt.MouseButton.LeftButton:
                return  # never swap the layers out from under an active drag
            # the release was missed (e.g. focus lost); recover
            self._drag_active = False

        canvas_w, canvas_h = layers.canvas_size
        self._layered = True
        self._item.setVisible(False)
        self._canvas.setRect(QRectF(0, 0, canvas_w, canvas_h))
        red, green, blue, alpha = layers.pad_rgba
        self._canvas.setBrush(
            QBrush(QColor(red, green, blue, alpha)) if alpha else QBrush())
        self._backdrop_item.setPixmap(QPixmap.fromImage(layers.backdrop))
        self._subject_item.setPixmap(QPixmap.fromImage(layers.subject))
        # "In front": backdrop below the subject. "Behind": the backdrop covers the subject.
        self._backdrop_item.setZValue(1 if layers.behind else 0)
        self._subject_item.setZValue(0 if layers.behind else 1)
        self._origin = layers.origin
        self._subject_item.setPos(
            layers.origin[0] + layers.offset[0] * canvas_w,
            layers.origin[1] + layers.offset[1] * canvas_h,
        )
        self._canvas.setVisible(True)
        self._scene.setSceneRect(QRectF(0, 0, canvas_w, canvas_h))
        self._hint.show()
        if self._fit:
            self.fit_to_view()
        else:
            self._update_sampling()
        self._position_overlay()
        self.viewport().update()

    def set_message(self, text: str) -> None:
        """Show a status message (empty string hides it)."""
        self._overlay.setText(text)
        self._overlay.setVisible(bool(text))
        self._position_overlay()

    # ------------------------------------------------------------------ #
    # Moving the subject
    # ------------------------------------------------------------------ #
    def _on_subject_pressed(self) -> None:
        self._drag_active = True

    def _on_subject_released(self) -> None:
        self._drag_active = False
        self._clamp_subject()
        self._emit_offset()

    def _clamp_subject(self) -> None:
        """Keep at least the subject's centre on the canvas so it can't be lost off-screen."""
        pixmap = self._subject_item.pixmap()
        rect = self._canvas.rect()
        half_w, half_h = pixmap.width() / 2.0, pixmap.height() / 2.0
        x = min(max(self._subject_item.x(), -half_w), rect.width() - half_w)
        y = min(max(self._subject_item.y(), -half_h), rect.height() - half_h)
        self._subject_item.setPos(x, y)

    def _emit_offset(self) -> None:
        rect = self._canvas.rect()
        if rect.width() <= 0 or rect.height() <= 0:
            return
        dx = (self._subject_item.x() - self._origin[0]) / rect.width()
        dy = (self._subject_item.y() - self._origin[1]) / rect.height()
        self.subject_moved.emit(float(dx), float(dy))

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self._layered and event.key() in (
            Qt.Key.Key_Left,
            Qt.Key.Key_Right,
            Qt.Key.Key_Up,
            Qt.Key.Key_Down,
        ):
            step = NUDGE_STEP_LARGE if event.modifiers(
            ) & Qt.KeyboardModifier.ShiftModifier else NUDGE_STEP
            rect = self._canvas.rect()
            dx = {Qt.Key.Key_Left: -step,
                  Qt.Key.Key_Right: step}.get(event.key(), 0.0)
            dy = {Qt.Key.Key_Up: -step,
                  Qt.Key.Key_Down: step}.get(event.key(), 0.0)
            self._subject_item.moveBy(dx * rect.width(), dy * rect.height())
            self._clamp_subject()
            self._emit_offset()
            event.accept()
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------ #
    # Zoom
    # ------------------------------------------------------------------ #
    def fit_to_view(self) -> None:
        self._fit = True
        rect = self._content_item().boundingRect()
        if rect.isEmpty():
            return
        viewport = self.viewport().rect()
        margin = 24
        scale_x = max(1, viewport.width() - margin) / rect.width()
        scale_y = max(1, viewport.height() - margin) / rect.height()
        self._apply_zoom(max(self.MIN_ZOOM, min(
            scale_x, scale_y, self.MAX_FIT_ZOOM)))
        self.centerOn(self._content_item())

    def actual_size(self) -> None:
        self._fit = False
        self._apply_zoom(1.0)
        self.centerOn(self._content_item())

    def zoom_by(self, factor: float, under_mouse: bool = False) -> None:
        if not self.has_image:
            return
        self._fit = False
        target = max(self.MIN_ZOOM, min(self.MAX_ZOOM, self._zoom * factor))
        self.setTransformationAnchor(
            QGraphicsView.ViewportAnchor.AnchorUnderMouse
            if under_mouse
            else QGraphicsView.ViewportAnchor.AnchorViewCenter
        )
        relative = target / self._zoom
        self.scale(relative, relative)
        self._zoom = target
        self._update_sampling()
        self.zoom_changed.emit(self._zoom)

    def _apply_zoom(self, zoom: float) -> None:
        self.resetTransform()
        self.scale(zoom, zoom)
        self._zoom = zoom
        self._update_sampling()
        self.zoom_changed.emit(zoom)

    def _update_sampling(self) -> None:
        # Nearest-neighbour when magnifying so individual pixels can be inspected.
        mode = (
            Qt.TransformationMode.FastTransformation
            if self._zoom >= 2.0
            else Qt.TransformationMode.SmoothTransformation
        )
        for item in (self._item, self._backdrop_item, self._subject_item):
            item.setTransformationMode(mode)

    # ------------------------------------------------------------------ #
    # Events
    # ------------------------------------------------------------------ #
    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        if not self.has_image or delta == 0:
            event.ignore()
            return
        self.zoom_by(1.15 ** (delta / 120.0), under_mouse=True)
        event.accept()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if self._fit and self.has_image:
            self.fit_to_view()
        self._position_overlay()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.has_image:
            if self._fit:
                self.actual_size()
            else:
                self.fit_to_view()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        painter.fillRect(rect, QColor(COLORS.bg))
        if not self.has_image:
            return
        target = self.mapFromScene(
            self._content_item().sceneBoundingRect()).boundingRect()
        painter.save()
        painter.resetTransform()  # draw the checkerboard in viewport pixels, not scene units
        painter.drawTiledPixmap(target, self._checker, QPoint(0, 0))
        painter.restore()

    def _position_overlay(self) -> None:
        viewport = self.viewport()
        if self._hint.isVisible():
            self._hint.adjustSize()
            self._hint.move(max(8, (viewport.width() - self._hint.width()) // 2),
                            max(8, viewport.height() - self._hint.height() - 12))
            self._hint.raise_()
        if not self._overlay.isVisible():
            return
        self._overlay.setMaximumWidth(max(120, viewport.width() - 40))
        self._overlay.adjustSize()
        if self.has_image:
            self._overlay.move(
                max(8, (viewport.width() - self._overlay.width()) // 2), 14)
        else:
            self._overlay.move(
                (viewport.width() - self._overlay.width()) // 2,
                (viewport.height() - self._overlay.height()) // 2,
            )
        self._overlay.raise_()

    # Dropping files anywhere over the viewer imports them.
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if mime_is_importable(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if mime_is_importable(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        payload = parse_mime(event.mimeData())
        if payload.is_empty:
            event.ignore()
            return
        event.acceptProposedAction()
        self.payload_dropped.emit(payload)
