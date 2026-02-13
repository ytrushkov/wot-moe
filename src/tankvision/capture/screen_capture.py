"""Screen capture using mss (cross-platform)."""

import logging

import mss
import numpy as np

logger = logging.getLogger(__name__)


class ScreenCapture:
    """Captures a region of the screen at a target sample rate.

    Args:
        roi: Tuple of (x, y, width, height) defining the region of interest.
        sample_rate: Target captures per second (used by the caller for sleep timing).
    """

    def __init__(self, roi: tuple[int, int, int, int], sample_rate: float = 2.0) -> None:
        self.roi = roi
        self.sample_rate = sample_rate
        self._sct = mss.mss()

    @property
    def monitor(self) -> dict[str, int]:
        """Build an mss monitor dict from the ROI."""
        x, y, w, h = self.roi
        return {"left": x, "top": y, "width": w, "height": h}

    def grab_frame(self) -> np.ndarray | None:
        """Capture a single frame from the ROI.

        Returns:
            BGR numpy array (H, W, 3) or None on failure.
        """
        try:
            screenshot = self._sct.grab(self.monitor)
            # mss returns BGRA; drop the alpha channel -> BGR for OpenCV
            frame = np.array(screenshot, dtype=np.uint8)
            return frame[:, :, :3]
        except Exception:
            logger.exception("Screen capture failed")
            return None

    def set_roi(self, roi: tuple[int, int, int, int]) -> None:
        """Update the capture region."""
        self.roi = roi

    def close(self) -> None:
        self._sct.close()
