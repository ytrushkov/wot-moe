"""Garage screen OCR: reads the selected tank name via PaddleOCR."""

import difflib
import logging
from pathlib import Path

import numpy as np

from tankvision.capture.screen_capture import ScreenCapture

logger = logging.getLogger(__name__)


class GarageDetector:
    """Continuously reads the tank name from the garage screen.

    Uses PaddleOCR to extract text from a user-defined ROI, then fuzzy-matches
    against the WG encyclopedia to resolve a ``tank_id``.

    Args:
        roi: Screen region (x, y, width, height) covering the tank name.
        vehicle_lookup: Mapping of ``{lowercase_name: (tank_id, display_name)}``
            built from the WG encyclopedia.
    """

    def __init__(
        self,
        roi: tuple[int, int, int, int],
        vehicle_lookup: dict[str, tuple[int, str]],
    ) -> None:
        self._capture = ScreenCapture(roi=roi, sample_rate=1.0)
        self._vehicle_lookup = vehicle_lookup
        # Build a list of candidate names for difflib matching
        self._vehicle_names = list(vehicle_lookup.keys())
        self._current_tank_id: int = 0
        self._current_tank_name: str = ""
        self._ocr = None  # Lazy-init PaddleOCR (heavy import)

    def _ensure_ocr(self):
        """Lazy-initialise PaddleOCR on first use."""
        if self._ocr is not None:
            return
        try:
            from paddleocr import PaddleOCR

            self._ocr = PaddleOCR(
                use_angle_cls=False,
                lang="en",
                show_log=False,
                use_gpu=False,
            )
            logger.info("PaddleOCR initialised for garage detection")
        except ImportError:
            raise ImportError(
                "PaddleOCR is required for garage detection. "
                "Install with: pip install 'wot-console-overlay[ocr-fallback]'"
            )

    def _ocr_frame(self, frame: np.ndarray) -> str:
        """Run PaddleOCR on a BGR frame and return the concatenated text."""
        self._ensure_ocr()
        results = self._ocr.ocr(frame, cls=False)
        if not results or not results[0]:
            return ""
        # PaddleOCR returns [[box, (text, confidence)], ...]
        texts = []
        for line in results[0]:
            text = line[1][0]
            confidence = line[1][1]
            if confidence >= 0.5:
                texts.append(text)
        return " ".join(texts).strip()

    def _match_vehicle(self, ocr_text: str) -> tuple[int, str] | None:
        """Fuzzy-match OCR text against known vehicle names.

        Returns:
            (tank_id, display_name) or None if no confident match.
        """
        if not ocr_text:
            return None

        query = ocr_text.lower().strip()

        # Try exact match first
        if query in self._vehicle_lookup:
            return self._vehicle_lookup[query]

        # Fuzzy match
        matches = difflib.get_close_matches(query, self._vehicle_names, n=1, cutoff=0.6)
        if matches:
            return self._vehicle_lookup[matches[0]]

        return None

    def poll(self) -> tuple[int, str] | None:
        """Capture the garage screen and read the tank name.

        Returns:
            (tank_id, tank_name) if a tank was detected, None otherwise.
        """
        frame = self._capture.grab_frame()
        if frame is None:
            return None

        ocr_text = self._ocr_frame(frame)
        if not ocr_text:
            logger.debug("Garage OCR: no text detected")
            return None

        match = self._match_vehicle(ocr_text)
        if match is None:
            logger.debug("Garage OCR: '%s' did not match any vehicle", ocr_text)
            return None

        tank_id, tank_name = match
        return (tank_id, tank_name)

    def detect_switch(self) -> tuple[int, str] | None:
        """Poll and return the new tank only if it changed.

        Returns:
            (tank_id, tank_name) if the tank changed since last call, None otherwise.
        """
        result = self.poll()
        if result is None:
            return None

        tank_id, tank_name = result
        if tank_id == self._current_tank_id:
            return None

        old_name = self._current_tank_name or "(none)"
        self._current_tank_id = tank_id
        self._current_tank_name = tank_name
        logger.info("Tank switch detected: %s → %s", old_name, tank_name)
        return (tank_id, tank_name)

    @property
    def current_tank_id(self) -> int:
        return self._current_tank_id

    @property
    def current_tank_name(self) -> str:
        return self._current_tank_name

    def set_roi(self, roi: tuple[int, int, int, int]) -> None:
        """Update the capture region."""
        self._capture.set_roi(roi)

    def close(self) -> None:
        self._capture.close()


def build_vehicle_lookup(vehicles: dict[str, dict]) -> dict[str, tuple[int, str]]:
    """Build a lookup dict from the WG encyclopedia response.

    Args:
        vehicles: Dict from ``WargamingApi.get_vehicles()`` mapping
            string tank_id to vehicle info dicts.

    Returns:
        ``{lowercase_name: (tank_id, display_name)}`` for both
        ``short_name`` and ``name`` fields.
    """
    lookup: dict[str, tuple[int, str]] = {}
    for str_id, info in vehicles.items():
        try:
            tank_id = int(str_id)
        except (ValueError, TypeError):
            continue
        short_name = info.get("short_name", "")
        full_name = info.get("name", "")
        display = short_name or full_name or f"Tank #{tank_id}"
        if short_name:
            lookup[short_name.lower()] = (tank_id, display)
        if full_name and full_name.lower() != short_name.lower():
            lookup[full_name.lower()] = (tank_id, display)
    return lookup
