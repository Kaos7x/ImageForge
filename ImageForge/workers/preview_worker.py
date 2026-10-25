"""Tasks that produce the centre-panel previews on a small thread pool."""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from PIL import Image

from core.compositor import BackgroundProvider, compose_layers, subject_origin
from core.encoder import estimate_encoded_size
from core.pipeline import ImagePipeline, ProcessingCancelled
from core.resize import LayoutPlan, resize_image
from models.options import LayerOrder, ProcessingOptions
from services.model_manager import ModelLoadError
from utils.constants import PREVIEW_MAX_DIMENSION
from utils.image_utils import cap_dimension, load_image_rgba
from utils.qt_image import LayerPreview, pil_to_qimage

logger = logging.getLogger(__name__)


class PreviewSignals(QObject):
    """Signals shared by all preview tasks. ``token`` lets the UI discard stale results."""

    before_ready = pyqtSignal(int, object)  # token, QImage
    after_ready = pyqtSignal(int, object, int, int, int)  # token, QImage, est_bytes, width, height
    failed = pyqtSignal(int, str, str)  # token, "before"|"after", message


def build_layer_preview(
    subject: Image.Image,
    plan: LayoutPlan,
    background: BackgroundProvider,
    order: LayerOrder,
    offset: tuple[float, float],
) -> LayerPreview:
    """Split the composition into movable layers, downscaled so every side is <= the preview cap."""
    canvas_w, canvas_h = plan.canvas_size
    scale = min(1.0, PREVIEW_MAX_DIMENSION / max(canvas_w, canvas_h, subject.width, subject.height))

    def scaled(size: tuple[int, int]) -> tuple[int, int]:
        return max(1, round(size[0] * scale)), max(1, round(size[1] * scale))

    canvas = scaled(plan.canvas_size)
    if scale < 1.0:
        subject = resize_image(subject, scaled(subject.size))
    origin_x, origin_y = subject_origin(plan)
    return LayerPreview(
        canvas_size=canvas,
        pad_rgba=plan.pad_color,
        backdrop=pil_to_qimage(background.scaled(canvas)),
        subject=pil_to_qimage(subject),
        origin=(round(origin_x * scale), round(origin_y * scale)),
        behind=order is LayerOrder.BEHIND,
        offset=offset,
    )


class BeforeTask(QRunnable):
    """Decodes the original image for the "Before" view."""

    def __init__(self, token: int, path: Path, signals: PreviewSignals) -> None:
        super().__init__()
        self._token, self._path, self._signals = token, path, signals

    def run(self) -> None:
        try:
            image = cap_dimension(load_image_rgba(self._path), PREVIEW_MAX_DIMENSION)
            self._signals.before_ready.emit(self._token, pil_to_qimage(image))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Before-preview failed for %s: %s", self._path, exc)
            self._signals.failed.emit(self._token, "before", f"Could not open the image: {exc}")


class AfterTask(QRunnable):
    """Runs the real pipeline (without saving) for the "After" view and size estimate."""

    def __init__(
        self,
        token: int,
        path: Path,
        options: ProcessingOptions,
        pipeline: ImagePipeline,
        cancel: threading.Event,
        signals: PreviewSignals,
        offset: tuple[float, float] = (0.0, 0.0),
    ) -> None:
        super().__init__()
        self._token = token
        self._path = path
        self._options = options
        self._pipeline = pipeline
        self._cancel = cancel
        self._signals = signals
        self._offset = offset

    def run(self) -> None:
        try:
            background = (
                BackgroundProvider.shared(Path(self._options.background_path))
                if self._options.background_active()
                else None
            )
            if background is not None:
                # Layered preview: keep the subject separate so the UI can drag it live.
                subject, plan = self._pipeline.prepare(
                    self._path, self._options, background, self._cancel, use_cache=True
                )
                image = compose_layers(subject, plan, background, self._options.layer_order, self._offset)
                payload: object = build_layer_preview(
                    subject, plan, background, self._options.layer_order, self._offset
                )
            else:
                image = self._pipeline.render(
                    self._path, self._options, None, self._cancel, use_cache=True
                )
                payload = pil_to_qimage(cap_dimension(image, PREVIEW_MAX_DIMENSION))
            if self._cancel.is_set():
                return
            estimate = estimate_encoded_size(image, self._options)
            if self._cancel.is_set():
                return
            self._signals.after_ready.emit(self._token, payload, estimate, image.width, image.height)
        except ProcessingCancelled:
            return
        except ModelLoadError as exc:
            self._signals.failed.emit(self._token, "after", str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.warning("After-preview failed for %s: %s", self._path, exc, exc_info=True)
            self._signals.failed.emit(self._token, "after", str(exc) or exc.__class__.__name__)
