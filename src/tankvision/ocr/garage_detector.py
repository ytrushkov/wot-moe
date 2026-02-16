"""Garage screen OCR: reads the selected tank name via Tesseract or PaddleOCR."""

import difflib
import logging
import platform
from pathlib import Path

import numpy as np

from tankvision.capture.screen_capture import ScreenCapture

logger = logging.getLogger(__name__)


class GarageDetector:
    """Continuously reads the tank name from the garage screen.

    Uses Tesseract (preferred on macOS) or PaddleOCR to extract text from a
    user-defined ROI, then fuzzy-matches against the WG encyclopedia to resolve
    a ``tank_id``.

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
        self._ocr_backend: str | None = None  # "tesseract" or "paddle"
        self._ocr = None  # Lazy-init OCR engine
        self._ocr_unavailable = False  # True after all backends failed
        self.last_frame: np.ndarray | None = None  # Last captured frame for preview

    def _ensure_ocr(self) -> bool:
        """Lazy-initialise OCR backend on first use.

        Tries Tesseract first (better for macOS ARM), falls back to PaddleOCR.
        Returns True if OCR is ready, False if unavailable.
        Logs a warning once on the first failure, then stays silent.
        """
        if self._ocr is not None:
            return True
        if self._ocr_unavailable:
            return False

        # Try Tesseract first (preferred on macOS, works on ARM)
        if self._try_tesseract():
            return True

        # Fall back to PaddleOCR
        if self._try_paddleocr():
            return True

        # All backends failed
        self._ocr_unavailable = True
        is_arm = platform.machine() == "arm64"
        hint = (
            "On macOS ARM: brew install tesseract && pip install pytesseract\n"
            "             Or use Rosetta: arch -x86_64 python3 -m venv venv-x86"
            if is_arm
            else "pip install 'wot-console-overlay[ocr-fallback]'"
        )
        logger.warning(
            "No OCR backend available — garage tank-name detection is disabled.\n%s",
            hint,
        )
        return False

    def _try_tesseract(self) -> bool:
        """Try to initialize pytesseract. Returns True on success."""
        try:
            import pytesseract
            from PIL import Image

            # Quick smoke test: check if tesseract binary is available
            pytesseract.get_tesseract_version()
            self._ocr = pytesseract
            self._ocr_backend = "tesseract"
            logger.info("Tesseract OCR initialised for garage detection")
            return True
        except (ImportError, FileNotFoundError, pytesseract.TesseractNotFoundError):
            return False

    def _try_paddleocr(self) -> bool:
        """Try to initialize PaddleOCR. Returns True on success."""
        try:
            from paddleocr import PaddleOCR

            self._ocr = PaddleOCR(
                use_angle_cls=False,
                lang="en",
                show_log=False,
            )
            self._ocr_backend = "paddle"
            logger.info("PaddleOCR initialised for garage detection")
            return True
        except (ImportError, ValueError, Exception):
            return False

    def _ocr_frame(self, frame: np.ndarray) -> str:
        """Run OCR on a BGR frame and return the concatenated text."""
        if not self._ensure_ocr():
            return ""

        if self._ocr_backend == "tesseract":
            return self._ocr_frame_tesseract(frame)
        elif self._ocr_backend == "paddle":
            return self._ocr_frame_paddle(frame)
        return ""

    def _ocr_frame_tesseract(self, frame: np.ndarray) -> str:
        """Run Tesseract OCR on a BGR numpy array."""
        import pytesseract
        from PIL import Image

        # Convert BGR → RGB
        rgb = frame[:, :, ::-1]
        pil_img = Image.fromarray(rgb)
        text = pytesseract.image_to_string(pil_img, config="--psm 7").strip()
        return text

    def _ocr_frame_paddle(self, frame: np.ndarray) -> str:
        """Run PaddleOCR on a BGR numpy array."""
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
        self.last_frame = frame

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
