"""Image preprocessing for OCR: grayscale, threshold, upscale."""

import cv2
import numpy as np


def preprocess_for_ocr(
    frame: np.ndarray,
    upscale_factor: int = 2,
    threshold_value: int = 200,
) -> np.ndarray:
    """Preprocess a BGR frame for digit template matching.

    Steps:
        1. Convert to grayscale
        2. Binary inverse threshold (white text on dark background -> black on white)
        3. Upscale for better template matching resolution

    Args:
        frame: BGR image (H, W, 3).
        upscale_factor: Factor to upscale the image. Use 1 to skip.
        threshold_value: Pixel intensity above which text is considered foreground.

    Returns:
        Binary image (H*scale, W*scale) with text as black pixels on white background.
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    _, binary = cv2.threshold(gray, threshold_value, 255, cv2.THRESH_BINARY)

    if upscale_factor > 1:
        h, w = binary.shape
        binary = cv2.resize(
            binary,
            (w * upscale_factor, h * upscale_factor),
            interpolation=cv2.INTER_NEAREST,
        )

    return binary


def extract_digit_regions(binary: np.ndarray, min_area: int = 50) -> list[tuple[np.ndarray, int]]:
    """Segment individual digit regions from a binary image using contour detection.

    Args:
        binary: Binary image with text as black pixels on white background (from preprocess_for_ocr).
        min_area: Minimum contour area to consider as a digit (filters noise).

    Returns:
        List of (digit_image, x_position) tuples sorted left-to-right by x position.
        Each digit_image is a cropped binary region containing a single character.
    """
    # Invert so digits are white (findContours expects white objects on black)
    inverted = cv2.bitwise_not(binary)

    contours, _ = cv2.findContours(inverted, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    regions = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < min_area:
            continue

        x, y, w, h = cv2.boundingRect(contour)
        digit_img = binary[y : y + h, x : x + w]
        regions.append((digit_img, x))

    # Sort left-to-right by x position to preserve digit order
    regions.sort(key=lambda r: r[1])

    return regions
