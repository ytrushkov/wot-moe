"""OCR pipeline: preprocess -> segment -> template match -> parse number."""

import logging
from dataclasses import dataclass
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
