"""AI background removal (rembg) with alpha clean-up and edge de-contamination.

rembg predicts a soft matte; we then
  1. snap near-0 / near-255 alpha values (removes the faint "haze" the model leaves),
  2. delete tiny disconnected specks while keeping real strands of hair,
  3. replace the colour of semi-transparent edge pixels with the colour of the nearby
     solid subject, which removes the old background's colour fringe,
  4. recombine the result with the *original* full-resolution RGB data.
"""
from __future__ import annotations

import logging
from typing import Any, Protocol

import cv2
import numpy as np
from PIL import Image

from models.options import BackgroundQuality
from utils.constants import MATTING_MAX_PIXELS

logger = logging.getLogger(__name__)


class BackgroundRemovalError(RuntimeError):
    """Raised when the model cannot be loaded or inference fails."""


class SessionProvider(Protocol):
    def model_for(self, quality: BackgroundQuality) -> str: ...

    def get_session(self, model_name: str) -> Any: ...


class BackgroundRemover:
    def __init__(self, sessions: SessionProvider) -> None:
        self._sessions = sessions

    def remove(self, image: Image.Image, quality: BackgroundQuality) -> Image.Image:
        """Return ``image`` (RGBA) with the background made transparent."""
        session = self._sessions.get_session(self._sessions.model_for(quality))
        try:
            from rembg import remove as rembg_remove
        except (Exception, SystemExit) as exc:  # noqa: BLE001
            raise BackgroundRemovalError(f"rembg could not be imported: {exc}") from exc

        rgba = image if image.mode == "RGBA" else image.convert("RGBA")
        rgb = rgba.convert("RGB")
        use_matting = quality is BackgroundQuality.BEST and rgb.width * rgb.height <= MATTING_MAX_PIXELS

        try:
            model_alpha = self._predict_alpha(rembg_remove, rgb, session, use_matting)
        except BackgroundRemovalError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise BackgroundRemovalError(f"Background removal failed: {exc}") from exc

        original_alpha = np.asarray(rgba.getchannel("A"))
        alpha = np.minimum(model_alpha, original_alpha)  # keep pre-existing transparency
        return build_cutout(np.asarray(rgb), alpha)

    # ------------------------------------------------------------------ #
    @staticmethod
    def _predict_alpha(rembg_remove: Any, rgb: Image.Image, session: Any, matting: bool) -> np.ndarray:
        if matting:
            try:
                cutout = rembg_remove(
                    rgb,
                    session=session,
                    post_process_mask=False,
                    alpha_matting=True,
                    alpha_matting_foreground_threshold=240,
                    alpha_matting_background_threshold=10,
                    alpha_matting_erode_size=10,
                )
                return np.asarray(cutout.convert("RGBA").getchannel("A"), dtype=np.uint8)
            except Exception as exc:  # noqa: BLE001 - pymatting can fail on odd masks
                logger.warning("Alpha matting failed (%s); using the plain matte instead", exc)
        # post_process_mask=False: rembg's built-in post-processing binarises the matte,
        # which destroys fine hair detail. We refine the alpha ourselves.
        mask = rembg_remove(rgb, session=session, only_mask=True, post_process_mask=False)
        return np.asarray(mask.convert("L"), dtype=np.uint8)


# --------------------------------------------------------------------------- #
# Alpha refinement (pure NumPy/OpenCV - unit-testable without rembg)
# --------------------------------------------------------------------------- #
def build_cutout(rgb: np.ndarray, alpha: np.ndarray) -> Image.Image:
    """Combine original ``rgb`` (H,W,3 uint8) with a refined ``alpha`` (H,W uint8)."""
    alpha = refine_alpha(alpha)
    cleaned_rgb = decontaminate_edges(rgb, alpha)
    rgba = np.dstack([cleaned_rgb, alpha])
    rgba[alpha == 0, :3] = 0  # transparent pixels carry no colour -> smaller PNGs
    return Image.fromarray(rgba)


def refine_alpha(alpha: np.ndarray) -> np.ndarray:
    alpha = np.ascontiguousarray(alpha, dtype=np.uint8)
    lut = np.arange(256, dtype=np.uint8)
    lut[:6] = 0
    lut[250:] = 255
    alpha = cv2.LUT(alpha, lut)
    return _remove_specks(alpha)


def _remove_specks(alpha: np.ndarray) -> np.ndarray:
    """Remove very small isolated blobs (model noise) but keep substantial parts."""
    mask = (alpha > 40).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if count <= 2:  # background + at most one component
        return alpha
    areas = stats[1:, cv2.CC_STAT_AREA]
    threshold = max(16, int(areas.max() * 0.0004))
    keep = np.concatenate(([False], areas >= threshold))
    if keep[1:].all():
        return alpha
    kept = keep[labels].astype(np.uint8)
    # Protect the semi-transparent fringe that surrounds kept regions.
    protected = cv2.dilate(kept, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))) > 0
    return np.where(protected, alpha, 0).astype(np.uint8)


def decontaminate_edges(rgb: np.ndarray, alpha: np.ndarray, sigma: float = 2.0, strip: int = 512) -> np.ndarray:
    """Replace colours of semi-transparent pixels with nearby solid-subject colours.

    Uses normalised convolution weighted by alpha^3 so opaque pixels dominate. Works in
    horizontal strips so memory stays small even for very large images.
    """
    out = np.array(rgb, dtype=np.uint8, copy=True)
    height = rgb.shape[0]
    margin = int(sigma * 3) + 1
    for y0 in range(0, height, strip):
        y1 = min(height, y0 + strip)
        edge = (alpha[y0:y1] > 0) & (alpha[y0:y1] < 255)
        if not edge.any():
            continue
        s0, s1 = max(0, y0 - margin), min(height, y1 + margin)
        weight = (alpha[s0:s1].astype(np.float32) / 255.0) ** 3
        numerator = cv2.GaussianBlur(rgb[s0:s1].astype(np.float32) * weight[..., None], (0, 0), sigma)
        denominator = cv2.GaussianBlur(weight, (0, 0), sigma)
        valid = denominator > 1e-4
        estimate = numerator / np.maximum(denominator, 1e-4)[..., None]
        a = alpha[y0:y1].astype(np.float32)[..., None] / 255.0
        original = rgb[y0:y1].astype(np.float32)
        local_estimate = estimate[y0 - s0 : y1 - s0]
        local_valid = valid[y0 - s0 : y1 - s0]
        blended = original * a + local_estimate * (1.0 - a)  # opaque -> original, clear -> estimate
        use = (edge & local_valid)[..., None]
        out[y0:y1] = np.where(use, np.clip(blended, 0, 255), original).astype(np.uint8)
    return out
