"""Tests for template matching digit recognition."""

import tempfile
from pathlib import Path

import cv2
import numpy as np
import pytest

from tankvision.ocr.template_matcher import TemplateMatcher


def _make_digit_template(digit: str, size: int = 30) -> np.ndarray:
    """Create a synthetic grayscale digit template (black digit on white background).

    Uses OpenCV putText to render a digit character.
    """
    img = np.full((size, size), 255, dtype=np.uint8)
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = size / 40
    thickness = max(1, size // 15)
    text_size = cv2.getTextSize(digit, font, font_scale, thickness)[0]
    x = (size - text_size[0]) // 2
    y = (size + text_size[1]) // 2
    cv2.putText(img, digit, (x, y), font, font_scale, 0, thickness)
    return img


@pytest.fixture
def templates_dir(tmp_path: Path) -> Path:
    """Create a temporary directory with synthetic digit templates."""
    for i in range(10):
        template = _make_digit_template(str(i))
        cv2.imwrite(str(tmp_path / f"{i}.png"), template)
    return tmp_path


class TestTemplateMatcher:
    def test_loads_templates(self, templates_dir: Path):
        matcher = TemplateMatcher(templates_dir=templates_dir)
        assert len(matcher.templates) == 10

    def test_recognizes_matching_digit(self, templates_dir: Path):
        matcher = TemplateMatcher(templates_dir=templates_dir, confidence_threshold=0.5)
        digit_img = _make_digit_template("7", size=30)
        result = matcher.match_digit(digit_img)
        assert result is not None
        char, confidence = result
        assert char == "7"
        assert confidence >= 0.5

    def test_rejects_below_threshold(self, templates_dir: Path):
        matcher = TemplateMatcher(templates_dir=templates_dir, confidence_threshold=0.99)
        # A random noise image should not match any digit well
        noise = np.random.randint(0, 255, (30, 30), dtype=np.uint8)
        result = matcher.match_digit(noise)
        # With a very high threshold, random noise is likely rejected
        # (this is a probabilistic test but 0.99 threshold should be safe)
        if result is not None:
            _, confidence = result
            assert confidence >= 0.99

    def test_empty_templates_returns_none(self, tmp_path: Path):
        matcher = TemplateMatcher(templates_dir=tmp_path)  # empty dir
        digit_img = _make_digit_template("5")
        result = matcher.match_digit(digit_img)
        assert result is None

    def test_recognize_number_parses_digits(self, templates_dir: Path):
        matcher = TemplateMatcher(templates_dir=templates_dir, confidence_threshold=0.3)
        # Create a sequence of digit images representing "123"
        regions = [
            (_make_digit_template("1", size=30), 0),
            (_make_digit_template("2", size=30), 35),
            (_make_digit_template("3", size=30), 70),
        ]
        result = matcher.recognize_number(regions)
        assert result == 123

    def test_recognize_number_empty_input(self, templates_dir: Path):
        matcher = TemplateMatcher(templates_dir=templates_dir)
        result = matcher.recognize_number([])
        assert result is None

    def test_nan_guard(self, templates_dir: Path):
        """Template matching should handle NaN results gracefully."""
        matcher = TemplateMatcher(templates_dir=templates_dir, confidence_threshold=0.5)
        # A zero-size image would cause issues
        empty = np.zeros((0, 0), dtype=np.uint8)
        result = matcher.match_digit(empty)
        assert result is None


class TestMultiResolutionTemplates:
    def test_loads_multiple_templates_per_digit(self, tmp_path: Path):
        """Should load both 0.png and 0_1080p.png as templates for '0'."""
        template = _make_digit_template("0")
        cv2.imwrite(str(tmp_path / "0.png"), template)
        cv2.imwrite(str(tmp_path / "0_1080p.png"), template)

        matcher = TemplateMatcher(templates_dir=tmp_path)
        assert len(matcher.templates.get("0", [])) == 2
