"""Lazy, thread-safe management of rembg / ONNX Runtime sessions.

* Sessions are created once per model and shared by all worker threads (ONNX Runtime
  sessions are thread-safe for inference).
* CUDA is used automatically when ``onnxruntime-gpu`` is installed and a GPU is present;
  otherwise the CPU provider is used.
* Status changes are broadcast to listeners (the UI subscribes through a Qt bridge).
"""
from __future__ import annotations

import importlib.util
import logging
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from models.options import BackgroundQuality

logger = logging.getLogger(__name__)

# Quality mode -> rembg model. "Best" uses the same network as "Balanced" but adds alpha
# matting in the BackgroundRemover for superior hair / edge detail.
QUALITY_MODELS: dict[BackgroundQuality, str] = {
    BackgroundQuality.FAST: "u2netp",
    BackgroundQuality.BALANCED: "isnet-general-use",
    BackgroundQuality.BEST: "isnet-general-use",
}


class ModelState(Enum):
    UNAVAILABLE = "unavailable"
    IDLE = "idle"
    LOADING = "loading"
    READY = "ready"
    ERROR = "error"


@dataclass(frozen=True)
class ModelStatus:
    state: ModelState
    model_name: str = ""
    detail: str = ""


class ModelLoadError(RuntimeError):
    """Raised when a background-removal model cannot be created."""


class ModelManager:
    def __init__(self) -> None:
        self._sessions: dict[str, Any] = {}
        self._labels: dict[str, str] = {}
        self._load_lock = threading.Lock()
        self._listeners: list[Callable[[ModelStatus], None]] = []
        self._status = ModelStatus(
            ModelState.IDLE if self.is_available() else ModelState.UNAVAILABLE,
            detail="" if self.is_available() else "rembg is not installed",
        )

    # ------------------------------------------------------------------ #
    @staticmethod
    def is_available() -> bool:
        if "rembg" in sys.modules:
            return True
        try:
            return importlib.util.find_spec("rembg") is not None
        except (ImportError, ValueError):
            return False

    @staticmethod
    def model_for(quality: BackgroundQuality) -> str:
        return QUALITY_MODELS[quality]

    @staticmethod
    def providers() -> list[str]:
        """ONNX Runtime providers in priority order (GPU first when available)."""
        try:
            import onnxruntime as ort

            available = set(ort.get_available_providers())
        except Exception:  # noqa: BLE001
            return ["CPUExecutionProvider"]
        chain = [p for p in ("CUDAExecutionProvider",) if p in available]
        chain.append("CPUExecutionProvider")
        return chain

    def is_loaded(self, model_name: str) -> bool:
        return model_name in self._sessions

    @property
    def status(self) -> ModelStatus:
        return self._status

    def status_for(self, model_name: str) -> ModelStatus:
        if not self.is_available():
            return ModelStatus(ModelState.UNAVAILABLE, model_name, "rembg is not installed")
        if model_name in self._sessions:
            return ModelStatus(ModelState.READY, model_name, self._labels.get(model_name, "CPU"))
        return ModelStatus(ModelState.IDLE, model_name, "")

    # ------------------------------------------------------------------ #
    def add_listener(self, callback: Callable[[ModelStatus], None]) -> None:
        self._listeners.append(callback)

    def _publish(self, status: ModelStatus) -> None:
        self._status = status
        for callback in list(self._listeners):
            try:
                callback(status)
            except Exception:  # noqa: BLE001 - a bad listener must not break loading
                logger.exception("Model status listener failed")

    # ------------------------------------------------------------------ #
    def get_session(self, model_name: str) -> Any:
        """Return the shared session for ``model_name``, loading it on first use."""
        session = self._sessions.get(model_name)
        if session is not None:
            return session
        with self._load_lock:
            session = self._sessions.get(model_name)
            if session is not None:
                return session
            self._publish(ModelStatus(ModelState.LOADING, model_name, "first use may download the model"))
            try:
                session = self._create_session(model_name)
            except (Exception, SystemExit) as exc:  # noqa: BLE001 - rembg may call sys.exit
                message = str(exc) or exc.__class__.__name__
                logger.error("Could not load model %s: %s", model_name, message, exc_info=True)
                self._publish(ModelStatus(ModelState.ERROR, model_name, message))
                raise ModelLoadError(
                    f"The background-removal model '{model_name}' could not be loaded.\n{message}\n\n"
                    "The first run downloads the model, so an internet connection is required."
                ) from exc
            self._sessions[model_name] = session
            self._labels[model_name] = self._provider_label(session)
            logger.info("Model %s ready (%s)", model_name, self._labels[model_name])
            self._publish(ModelStatus(ModelState.READY, model_name, self._labels[model_name]))
            return session

    def _create_session(self, model_name: str) -> Any:
        from rembg import new_session

        providers = self.providers()
        try:
            return new_session(model_name, providers=providers)
        except TypeError:  # very old rembg without the ``providers`` argument
            return new_session(model_name)

    @staticmethod
    def _provider_label(session: Any) -> str:
        try:
            active = session.inner_session.get_providers()[0]
        except Exception:  # noqa: BLE001
            return "CPU"
        return "GPU (CUDA)" if active == "CUDAExecutionProvider" else "CPU"
