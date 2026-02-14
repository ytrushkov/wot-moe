"""Unit tests for GarageDetector and build_vehicle_lookup."""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from tankvision.ocr.garage_detector import GarageDetector, build_vehicle_lookup


# -------------------------------------------------------------------
# build_vehicle_lookup
# -------------------------------------------------------------------


class TestBuildVehicleLookup:
    def test_builds_from_short_name(self):
        vehicles = {
            "123": {"short_name": "T-54", "name": "T-54 ltwt."},
        }
        lookup = build_vehicle_lookup(vehicles)
        assert "t-54" in lookup
        assert lookup["t-54"] == (123, "T-54")

    def test_builds_from_full_name(self):
        vehicles = {
            "456": {"short_name": "M2 Med.", "name": "M2 Medium Tank"},
        }
        lookup = build_vehicle_lookup(vehicles)
        assert "m2 med." in lookup
        assert "m2 medium tank" in lookup

    def test_display_name_prefers_short(self):
        vehicles = {
            "789": {"short_name": "Obj. 140", "name": "Object 140"},
        }
        lookup = build_vehicle_lookup(vehicles)
        _, display = lookup["obj. 140"]
        assert display == "Obj. 140"

    def test_empty_vehicles(self):
        assert build_vehicle_lookup({}) == {}

    def test_skips_invalid_ids(self):
        vehicles = {"abc": {"short_name": "Test"}}
        assert build_vehicle_lookup(vehicles) == {}

    def test_multiple_vehicles(self):
        vehicles = {
            "1": {"short_name": "A", "name": "Alpha"},
            "2": {"short_name": "B", "name": "Bravo"},
            "3": {"short_name": "C", "name": "Charlie"},
        }
        lookup = build_vehicle_lookup(vehicles)
        assert len(lookup) == 6  # short + full name for each


# -------------------------------------------------------------------
# GarageDetector fuzzy matching
# -------------------------------------------------------------------

SAMPLE_LOOKUP = {
    "m2 medium": (100, "M2 Medium"),
    "t-54": (200, "T-54"),
    "object 140": (300, "Object 140"),
    "dbv-152": (400, "DBV-152"),
    "is-7": (500, "IS-7"),
}


class TestGarageDetectorMatching:
    """Test the fuzzy matching logic without needing PaddleOCR or screen capture."""

    def _make_detector(self) -> GarageDetector:
        with patch.object(GarageDetector, "__init__", lambda self, **kw: None):
            det = GarageDetector.__new__(GarageDetector)
        det._vehicle_lookup = SAMPLE_LOOKUP
        det._vehicle_names = list(SAMPLE_LOOKUP.keys())
        det._current_tank_id = 0
        det._current_tank_name = ""
        det._ocr = None
        det._capture = MagicMock()
        return det

    def test_exact_match(self):
        det = self._make_detector()
        assert det._match_vehicle("M2 Medium") == (100, "M2 Medium")

    def test_case_insensitive(self):
        det = self._make_detector()
        assert det._match_vehicle("m2 MEDIUM") == (100, "M2 Medium")

    def test_fuzzy_match_close(self):
        det = self._make_detector()
        # OCR might return "M2 Mediun" (typo)
        result = det._match_vehicle("M2 Mediun")
        assert result is not None
        assert result[0] == 100

    def test_fuzzy_match_dbv(self):
        det = self._make_detector()
        # OCR might return "DBV-l52" (l instead of 1)
        result = det._match_vehicle("DBV-l52")
        assert result is not None
        assert result[0] == 400

    def test_no_match_garbage(self):
        det = self._make_detector()
        assert det._match_vehicle("xyzgarbagetext") is None

    def test_empty_text(self):
        det = self._make_detector()
        assert det._match_vehicle("") is None


# -------------------------------------------------------------------
# Tank switch detection
# -------------------------------------------------------------------


class TestTankSwitchDetection:
    def _make_detector(self) -> GarageDetector:
        with patch.object(GarageDetector, "__init__", lambda self, **kw: None):
            det = GarageDetector.__new__(GarageDetector)
        det._vehicle_lookup = SAMPLE_LOOKUP
        det._vehicle_names = list(SAMPLE_LOOKUP.keys())
        det._current_tank_id = 0
        det._current_tank_name = ""
        det._ocr = MagicMock()
        det._capture = MagicMock()
        return det

    def _mock_ocr(self, det: GarageDetector, text: str):
        """Make the detector's _ocr_frame return the given text."""
        det._ocr_frame = MagicMock(return_value=text)

    def test_first_detection_is_switch(self):
        det = self._make_detector()
        self._mock_ocr(det, "T-54")
        det._capture.grab_frame.return_value = np.zeros((100, 400, 3), dtype=np.uint8)

        result = det.detect_switch()
        assert result == (200, "T-54")
        assert det.current_tank_id == 200

    def test_same_tank_no_switch(self):
        det = self._make_detector()
        self._mock_ocr(det, "T-54")
        det._capture.grab_frame.return_value = np.zeros((100, 400, 3), dtype=np.uint8)

        det.detect_switch()  # First call sets current
        result = det.detect_switch()  # Same tank
        assert result is None

    def test_different_tank_triggers_switch(self):
        det = self._make_detector()
        det._capture.grab_frame.return_value = np.zeros((100, 400, 3), dtype=np.uint8)

        det._ocr_frame = MagicMock(return_value="T-54")
        det.detect_switch()

        det._ocr_frame = MagicMock(return_value="IS-7")
        result = det.detect_switch()
        assert result == (500, "IS-7")
        assert det.current_tank_id == 500

    def test_no_frame_returns_none(self):
        det = self._make_detector()
        det._capture.grab_frame.return_value = None

        assert det.detect_switch() is None

    def test_unrecognised_text_returns_none(self):
        det = self._make_detector()
        self._mock_ocr(det, "xyzgarbage")
        det._capture.grab_frame.return_value = np.zeros((100, 400, 3), dtype=np.uint8)

        assert det.detect_switch() is None


# -------------------------------------------------------------------
# Config garage section
# -------------------------------------------------------------------


class TestGarageConfig:
    def test_garage_defaults(self):
        from tankvision.config import DEFAULTS

        assert "garage" in DEFAULTS
        assert DEFAULTS["garage"]["roi_width"] == 0
        assert DEFAULTS["garage"]["poll_interval"] == 3.0

    def test_garage_enabled_check(self):
        from tankvision.__main__ import _garage_enabled

        assert not _garage_enabled({"garage": {"roi_width": 0, "roi_height": 0}})
        assert _garage_enabled({"garage": {"roi_width": 400, "roi_height": 60}})
        assert not _garage_enabled({"garage": {"roi_width": 400, "roi_height": 0}})
