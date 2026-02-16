"""OCR pipeline: preprocess -> segment -> template match -> parse number."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from tankvision.ocr.preprocessor import extract_digit_regions, preprocess_for_ocr
from tankvision.ocr.template_matcher import TemplateMatcher

logger = logging.getLogger(__name__)


@dataclass
class DamageReading:
    """Raw damage values read from the HUD."""

    direct_damage: int
    assisted_damage: int

    @property
    def combined(self) -> int:
        return self.direct_damage + self.assisted_damage


@dataclass
class OcrResult:
    """Extended result with debug information for the OCR preview window."""

    reading: DamageReading | None
    raw_frame: np.ndarray = field(repr=False)
    digit_confidences: list[tuple[str, float]] = field(default_factory=list)
    overall_confidence: float = 0.0


class OcrPipeline:
    """Orchestrates frame preprocessing, digit segmentation, and recognition.

    Args:
        confidence_threshold: Minimum template match score.
        templates_dir: Path to digit template images.
        upscale_factor: Image upscale factor for preprocessing.
        threshold_value: Binary threshold for text extraction.
    """

    def __init__(
        self,
        confidence_threshold: float = 0.8,
        templates_dir: Path | None = None,
        upscale_factor: int = 2,
        threshold_value: int = 200,
    ) -> None:
        self.upscale_factor = upscale_factor
        self.threshold_value = threshold_value

        kwargs = {"confidence_threshold": confidence_threshold}
        if templates_dir is not None:
            kwargs["templates_dir"] = templates_dir
        self.matcher = TemplateMatcher(**kwargs)

    def process_frame(self, frame: np.ndarray) -> DamageReading | None:
        """Run the full OCR pipeline on a captured frame.

        The frame should already be cropped to the ROI containing damage numbers.

        For now this reads a single number (combined damage).
        Future: support separate direct + assisted regions.

        Args:
            frame: BGR image of the damage number region.

        Returns:
            DamageReading with parsed values, or None if OCR fails.
        """
        binary = preprocess_for_ocr(
            frame,
            upscale_factor=self.upscale_factor,
            threshold_value=self.threshold_value,
        )

        regions = extract_digit_regions(binary)
        if not regions:
            logger.debug("No digit regions found in frame")
            return None

        value = self.matcher.recognize_number(regions)
        if value is None:
            return None

        # Currently treating the single recognized number as direct damage.
        # Assisted damage requires a second ROI or different HUD region.
        return DamageReading(direct_damage=value, assisted_damage=0)

    def process_frame_detailed(self, frame: np.ndarray) -> OcrResult:
        """Run the OCR pipeline and return debug info for the validation UI.

        Unlike process_frame(), this captures per-digit confidence scores
        and the raw frame for display in the OCR preview window.
        """
        binary = preprocess_for_ocr(
            frame,
            upscale_factor=self.upscale_factor,
            threshold_value=self.threshold_value,
        )

        regions = extract_digit_regions(binary)
        if not regions:
            return OcrResult(reading=None, raw_frame=frame)

        digit_confidences: list[tuple[str, float]] = []
        chars: list[str] = []

        for digit_img, _ in regions:
            result = self.matcher.match_digit(digit_img)
            if result is None:
                digit_confidences.append(("?", 0.0))
            else:
                char, conf = result
                digit_confidences.append((char, conf))
                if char not in (",", "."):
                    chars.append(char)

        reading = None
        if chars:
            try:
                value = int("".join(chars))
                reading = DamageReading(direct_damage=value, assisted_damage=0)
            except ValueError:
                pass

        avg_conf = (
            sum(c for _, c in digit_confidences) / len(digit_confidences)
            if digit_confidences
            else 0.0
        )

        return OcrResult(
            reading=reading,
            raw_frame=frame,
            digit_confidences=digit_confidences,
            overall_confidence=avg_conf,
        )
