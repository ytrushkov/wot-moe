"""Digit recognition via OpenCV template matching."""

import logging
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Characters we need to recognize
DIGIT_CHARS = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"]
SEPARATOR_CHARS = ["comma", "dot"]
ALL_CHARS = DIGIT_CHARS + SEPARATOR_CHARS

# Map template file stems to the character they represent
CHAR_MAP = {
    "0": "0", "1": "1", "2": "2", "3": "3", "4": "4",
    "5": "5", "6": "6", "7": "7", "8": "8", "9": "9",
    "comma": ",", "dot": ".",
}

DEFAULT_TEMPLATES_DIR = Path(__file__).parent.parent / "assets" / "digit_templates"


class TemplateMatcher:
    """Matches digit images against pre-captured templates.

    Templates are binary images (black text on white background) of individual
    characters from the WoT Console HUD font, stored as PNG files.

    Args:
        templates_dir: Path to directory containing template PNGs (0.png .. 9.png, comma.png, dot.png).
        confidence_threshold: Minimum match score (0.0 - 1.0) to accept a recognition.
    """

    def __init__(
        self,
        templates_dir: Path = DEFAULT_TEMPLATES_DIR,
        confidence_threshold: float = 0.8,
    ) -> None:
        self.confidence_threshold = confidence_threshold
        self.templates: dict[str, list[np.ndarray]] = {}
        self._load_templates(templates_dir)

    def _load_templates(self, templates_dir: Path) -> None:
        """Load template images from disk.

        Supports multiple templates per character (for different resolutions)
        by looking for files like 0.png, 0_1080p.png, 0_1440p.png, etc.
        """
        if not templates_dir.exists():
            logger.warning("Templates directory not found: %s", templates_dir)
            return

        for png_path in sorted(templates_dir.glob("*.png")):
            stem = png_path.stem.split("_")[0]  # "0_1080p" -> "0"
            if stem not in CHAR_MAP:
                continue

            char = CHAR_MAP[stem]
            img = cv2.imread(str(png_path), cv2.IMREAD_GRAYSCALE)
            if img is None:
                logger.warning("Failed to load template: %s", png_path)
                continue

            if char not in self.templates:
                self.templates[char] = []
            self.templates[char].append(img)

        logger.info("Loaded templates for %d characters", len(self.templates))

    def match_digit(self, digit_img: np.ndarray) -> tuple[str, float] | None:
        """Match a single digit image against all templates.

        Args:
            digit_img: Grayscale image of a single digit/character.

        Returns:
            (character, confidence) tuple, or None if no match above threshold.
        """
        if not self.templates:
            return None

        best_char: str | None = None
        best_score = -1.0

        for char, templates in self.templates.items():
            for template in templates:
                score = self._match_single(digit_img, template)
                if score is not None and score > best_score:
                    best_score = score
                    best_char = char

        if best_char is not None and best_score >= self.confidence_threshold:
            return (best_char, best_score)

        return None

    def _match_single(self, digit_img: np.ndarray, template: np.ndarray) -> float | None:
        """Compare a digit image to a single template.

        Resizes the template to match the digit image height, then runs
        cv2.matchTemplate with TM_CCOEFF_NORMED.

        Returns:
            Best match score (0.0 - 1.0), or None if matching fails.
        """
        dh, dw = digit_img.shape[:2]
        th, tw = template.shape[:2]

        if dh == 0 or dw == 0 or th == 0 or tw == 0:
            return None

        # Scale template to match digit height
        scale = dh / th
        new_tw = max(1, int(tw * scale))
        resized_template = cv2.resize(template, (new_tw, dh), interpolation=cv2.INTER_NEAREST)

        # Template can't be larger than the source
        rh, rw = resized_template.shape[:2]
        if rw > dw or rh > dh:
            return None

        result = cv2.matchTemplate(digit_img, resized_template, cv2.TM_CCOEFF_NORMED)

        # Guard against NaN (known Apple Silicon issue)
        if np.isnan(result).any():
            logger.debug("NaN in template matching result, skipping")
            return None

        _, max_val, _, _ = cv2.minMaxLoc(result)
        return float(max_val)

    def recognize_number(self, digit_regions: list[tuple[np.ndarray, int]]) -> int | None:
        """Recognize a multi-digit number from a list of segmented digit images.

        Args:
            digit_regions: List of (digit_image, x_position) from extract_digit_regions.

        Returns:
            The recognized integer, or None if recognition fails.
        """
        chars: list[str] = []

        for digit_img, _ in digit_regions:
            result = self.match_digit(digit_img)
            if result is None:
                logger.debug("Unrecognized digit region, aborting number")
                return None

            char, confidence = result
            logger.debug("Matched '%s' with confidence %.3f", char, confidence)

            # Skip separators (commas/dots are thousand separators)
            if char in (",", "."):
                continue

            chars.append(char)

        if not chars:
            return None

        try:
            return int("".join(chars))
        except ValueError:
            return None
