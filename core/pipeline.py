"""The image processing pipeline.

Stages (each can be cancelled between steps):

    load -> [downscale] -> background removal -> [upscale] -> layout/composite -> encode

Performance notes
* When the target is smaller than the source we downscale *before* running the neural
  network, so inference works on fewer pixels. When upscaling we remove the background
  first and upscale the cut-out, so the network never sees an inflated image.
* The live preview passes ``use_cache=True`` so changing e.g. the compression slider does
  not re-run background removal.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from PIL import Image

from core.background import BackgroundRemover, SessionProvider
from core.compositor import BackgroundProvider, compose_layers
from core.encoder import encode_to_file
from core.resize import LayoutPlan, plan_layout, resize_image
from models.options import ProcessingOptions
from models.results import ProcessResult
from services.output_allocator import OutputPathAllocator
from utils.image_utils import load_image_rgba
from utils.lru_cache import ThreadSafeLRU

logger = logging.getLogger(__name__)


class ProcessingCancelled(Exception):
    """Raised inside the pipeline when the user cancels."""


def _check(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise ProcessingCancelled()


class ImagePipeline:
    def __init__(self, sessions: SessionProvider) -> None:
        self._sessions = sessions
        self._remover = BackgroundRemover(sessions)
        self._source_cache: ThreadSafeLRU[tuple[str, int], Image.Image] = ThreadSafeLRU(2)
        self._subject_cache: ThreadSafeLRU[tuple, Image.Image] = ThreadSafeLRU(3)

    def warm_up(self, options: ProcessingOptions) -> None:
        """Load the neural network (if needed) before parallel work begins."""
        if options.remove_background:
            self._sessions.get_session(self._sessions.model_for(options.background_quality))

    # ------------------------------------------------------------------ #
    def prepare(
        self,
        path: Path,
        options: ProcessingOptions,
        background: BackgroundProvider | None,
        cancel: threading.Event | None = None,
        use_cache: bool = False,
    ) -> tuple[Image.Image, LayoutPlan]:
        """Load, scale and (optionally) cut out the subject. Returns it with its layout plan.

        This is everything *before* compositing, so the UI can show the subject as a movable
        layer and re-composite instantly when it is dragged.
        """
        source = self._load_source(path, use_cache)
        _check(cancel)
        plan = plan_layout(source.size, options, background is not None)
        subject = self._prepare_subject(path, source, plan, options, cancel, use_cache)
        _check(cancel)
        return subject, plan

    def render(
        self,
        path: Path,
        options: ProcessingOptions,
        background: BackgroundProvider | None,
        cancel: threading.Event | None = None,
        use_cache: bool = False,
        offset: tuple[float, float] = (0.0, 0.0),
    ) -> Image.Image:
        """Run every stage except saving; returns the final RGBA canvas."""
        subject, plan = self.prepare(path, options, background, cancel, use_cache)
        return compose_layers(subject, plan, background, options.layer_order, offset)

    def process_to_file(
        self,
        path: Path,
        stem: str,
        options: ProcessingOptions,
        background: BackgroundProvider | None,
        allocator: OutputPathAllocator,
        output_dir: Path,
        cancel: threading.Event | None = None,
        offset: tuple[float, float] = (0.0, 0.0),
    ) -> ProcessResult:
        """Render ``path`` and write it into ``output_dir`` using the naming convention."""
        image = self.render(path, options, background, cancel, offset=offset)
        _check(cancel)
        destination = allocator.reserve(output_dir, stem, image.size, options.output_format)
        try:
            written = encode_to_file(image, options, destination)
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        return ProcessResult(destination, image.size, written)

    # ------------------------------------------------------------------ #
    def _load_source(self, path: Path, use_cache: bool) -> Image.Image:
        if not use_cache:
            return load_image_rgba(path)
        key = (str(path), path.stat().st_mtime_ns)
        image = self._source_cache.get(key)
        if image is None:
            image = load_image_rgba(path)
            self._source_cache.put(key, image)
        return image

    def _prepare_subject(
        self,
        path: Path,
        source: Image.Image,
        plan: LayoutPlan,
        options: ProcessingOptions,
        cancel: threading.Event | None,
        use_cache: bool,
    ) -> Image.Image:
        key = None
        if use_cache:
            key = (
                str(path),
                path.stat().st_mtime_ns,
                options.remove_background,
                options.background_quality.value if options.remove_background else "",
                plan.scaled_size,
            )
            cached = self._subject_cache.get(key)
            if cached is not None:
                return cached

        target = plan.scaled_size
        needs_resize = target != source.size
        downscaling = needs_resize and target[0] * target[1] < source.width * source.height

        image = source
        if options.remove_background:
            if downscaling:
                image = resize_image(image, target)
                _check(cancel)
                image = self._remover.remove(image, options.background_quality)
            else:
                image = self._remover.remove(image, options.background_quality)
                _check(cancel)
                if needs_resize:
                    image = resize_image(image, target)
        elif needs_resize:
            image = resize_image(image, target)

        if key is not None:
            self._subject_cache.put(key, image)
        return image
