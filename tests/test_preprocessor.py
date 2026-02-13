"""Tests for image preprocessing."""

import numpy as np

from tankvision.ocr.preprocessor import extract_digit_regions, preprocess_for_ocr


def _make_white_text_image(width: int = 200, height: int = 50) -> np.ndarray:
    """Create a synthetic BGR image with white text-like blobs on a dark background."""
    img = np.zeros((height, width, 3), dtype=np.uint8)
    img[:] = (30, 30, 40)  # Dark background

    # Draw three white "digit" rectangles
    img[10:40, 20:45] = (255, 255, 255)   # digit 1
    img[10:40, 55:80] = (255, 255, 255)   # digit 2
    img[10:40, 90:115] = (255, 255, 255)  # digit 3
    return img


class TestPreprocessForOcr:
    def test_output_is_2d_binary(self):
        img = _make_white_text_image()
        result = preprocess_for_ocr(img, upscale_factor=1)
        assert result.ndim == 2
        unique_values = set(np.unique(result))
        assert unique_values.issubset({0, 255})

    def test_upscale_doubles_dimensions(self):
        img = _make_white_text_image(200, 50)
        result = preprocess_for_ocr(img, upscale_factor=2)
        assert result.shape == (100, 400)

    def test_upscale_factor_one_preserves_size(self):
        img = _make_white_text_image(200, 50)
        result = preprocess_for_ocr(img, upscale_factor=1)
        assert result.shape == (50, 200)

    def test_white_text_becomes_white_in_binary(self):
        """White text on dark background should produce white pixels after THRESH_BINARY."""
        img = _make_white_text_image()
        result = preprocess_for_ocr(img, upscale_factor=1, threshold_value=200)
        # White region in source (255) should be white (255) after threshold
        assert result[20, 30] == 255
        # Dark region should be black (0)
        assert result[20, 5] == 0


class TestExtractDigitRegions:
    def test_finds_three_digit_regions(self):
        img = _make_white_text_image()
        binary = preprocess_for_ocr(img, upscale_factor=1)
        # For extract_digit_regions, digits must be black on white.
        # preprocess_for_ocr with THRESH_BINARY produces white text on black bg.
        # Invert so digits are black on white (matching what the function expects).
        inverted = 255 - binary
        regions = extract_digit_regions(inverted, min_area=10)
        assert len(regions) == 3

    def test_sorted_left_to_right(self):
        img = _make_white_text_image()
        binary = preprocess_for_ocr(img, upscale_factor=1)
        inverted = 255 - binary
        regions = extract_digit_regions(inverted, min_area=10)
        x_positions = [x for _, x in regions]
        assert x_positions == sorted(x_positions)

    def test_filters_small_noise(self):
        img = _make_white_text_image()
        # Add a tiny noise dot
        img[5, 5] = (255, 255, 255)
        binary = preprocess_for_ocr(img, upscale_factor=1)
        inverted = 255 - binary
        regions = extract_digit_regions(inverted, min_area=50)
        # The tiny dot should be filtered out, leaving 3 digit regions
        assert len(regions) == 3

    def test_empty_image_returns_empty(self):
        img = np.zeros((50, 200, 3), dtype=np.uint8)  # All black
        binary = preprocess_for_ocr(img, upscale_factor=1)
        inverted = 255 - binary
        regions = extract_digit_regions(inverted, min_area=10)
        assert len(regions) == 0
