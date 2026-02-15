"""Visual ROI picker: transparent overlay where the user drags a rectangle."""

from __future__ import annotations

import sys
from pathlib import Path


_MODES = {
    "garage": {
        "title": "TankVision — Select Tank Name Region",
        "instruction": (
            "Drag a rectangle over the tank name in the garage.\n"
            "Press ENTER to confirm, ESC to cancel."
        ),
        "section": "garage",
    },
    "ocr": {
        "title": "TankVision — Select Damage Number Region",
        "instruction": (
            "Drag a rectangle over the damage numbers shown during battle.\n"
            "Include both direct and assisted damage areas.\n"
            "Press ENTER to confirm, ESC to cancel."
        ),
        "section": "ocr",
    },
}


def _save_roi_to_config(
    roi: tuple[int, int, int, int],
    config_path: Path,
    section: str = "garage",
) -> None:
    """Write the ROI into the given config section, preserving other settings."""
    x, y, w, h = roi
    section_header = f"[{section}]"

    # Read existing config or start fresh
    if config_path.exists():
        text = config_path.read_text()
    else:
        text = ""

    if section_header in text:
        # Replace existing roi values using line-by-line rewrite
        lines = text.splitlines(keepends=True)
        new_lines = []
        in_section = False
        keys_written: set[str] = set()
        for line in lines:
            stripped = line.strip()
            if stripped == section_header:
                in_section = True
                new_lines.append(line)
                continue
            elif stripped.startswith("[") and stripped.endswith("]"):
                # Entering a new section — write any missing keys first
                if in_section:
                    for key, val in [
                        ("roi_x", x), ("roi_y", y),
                        ("roi_width", w), ("roi_height", h),
                    ]:
                        if key not in keys_written:
                            new_lines.append(f"{key} = {val}\n")
                in_section = False
                new_lines.append(line)
                continue

            if in_section:
                for key, val in [
                    ("roi_x", x), ("roi_y", y),
                    ("roi_width", w), ("roi_height", h),
                ]:
                    if stripped.startswith(f"{key}"):
                        new_lines.append(f"{key} = {val}\n")
                        keys_written.add(key)
                        break
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)

        # If target section was the last section, write missing keys
        if in_section:
            for key, val in [
                ("roi_x", x), ("roi_y", y),
                ("roi_width", w), ("roi_height", h),
            ]:
                if key not in keys_written:
                    new_lines.append(f"{key} = {val}\n")

        text = "".join(new_lines)
    else:
        # Append new section
        if text and not text.endswith("\n"):
            text += "\n"
        text += (
            f"\n{section_header}\n"
            f"roi_x = {x}\n"
            f"roi_y = {y}\n"
            f"roi_width = {w}\n"
            f"roi_height = {h}\n"
        )
        if section == "garage":
            text += "poll_interval = 3.0\n"

    config_path.write_text(text)


def run_roi_picker(
    config_path: str = "config.toml",
    mode: str = "garage",
) -> tuple[int, int, int, int] | None:
    """Launch the ROI picker and save result to config.

    Args:
        config_path: Path to the TOML config file.
        mode: "garage" for tank name region, "ocr" for damage number region.

    Returns:
        The selected (x, y, width, height) or None if cancelled.
    """
    if mode not in _MODES:
        print(f"Unknown calibration mode '{mode}'. Use 'garage' or 'ocr'.")
        return None

    try:
        from PyQt6.QtCore import QPoint, QRect, Qt
        from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
        from PyQt6.QtWidgets import QApplication, QMainWindow
    except ImportError:
        print(
            "PyQt6 is required for the calibration UI.\n"
            "Install it with: pip install 'wot-console-overlay[ui]'"
        )
        return None

    import mss
    import numpy as np

    class RoiPickerWindow(QMainWindow):
        """Fullscreen window showing a screenshot with a selection overlay."""

        def __init__(
            self, cfg_path: str, picker_mode: str, background: QPixmap,
        ) -> None:
            super().__init__()
            self.config_path = Path(cfg_path)
            self._mode_info = _MODES.get(picker_mode, _MODES["garage"])
            self._bg = background
            self._start: QPoint | None = None
            self._end: QPoint | None = None
            self._confirmed = False

            self.setWindowTitle(self._mode_info["title"])
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
            )
            self.showFullScreen()

        def paintEvent(self, event) -> None:  # noqa: N802
            painter = QPainter(self)

            # Draw the desktop screenshot as background
            painter.drawPixmap(self.rect(), self._bg)

            # Semi-transparent dark tint over the whole screen
            painter.fillRect(self.rect(), QColor(0, 0, 0, 100))

            # Instructions
            painter.setPen(QPen(QColor(255, 255, 255)))
            font = QFont("Arial", 18)
            painter.setFont(font)
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter,
                "\n\n" + self._mode_info["instruction"],
            )

            # Draw the selection rectangle
            if self._start and self._end:
                rect = QRect(self._start, self._end).normalized()

                # Show the un-tinted screenshot in the selected area
                painter.drawPixmap(rect, self._bg, rect)

                # Draw border
                pen = QPen(QColor(0, 255, 0), 2)
                painter.setPen(pen)
                painter.drawRect(rect)

                # Show dimensions
                dim_font = QFont("Arial", 12)
                painter.setFont(dim_font)
                painter.setPen(QPen(QColor(0, 255, 0)))
                painter.drawText(
                    rect.x(),
                    rect.y() - 5,
                    f"{rect.width()}x{rect.height()} at ({rect.x()}, {rect.y()})",
                )

            painter.end()

        def mousePressEvent(self, event) -> None:  # noqa: N802
            if event.button() == Qt.MouseButton.LeftButton:
                self._start = event.pos()
                self._end = event.pos()
                self.update()

        def mouseMoveEvent(self, event) -> None:  # noqa: N802
            if self._start is not None:
                self._end = event.pos()
                self.update()

        def mouseReleaseEvent(self, event) -> None:  # noqa: N802
            if event.button() == Qt.MouseButton.LeftButton and self._start:
                self._end = event.pos()
                self.update()

        def keyPressEvent(self, event) -> None:  # noqa: N802
            if event.key() == Qt.Key.Key_Return or event.key() == Qt.Key.Key_Enter:
                if self._start and self._end:
                    self._confirmed = True
                    self.close()
            elif event.key() == Qt.Key.Key_Escape:
                self._confirmed = False
                self.close()

        @property
        def selected_roi(self) -> tuple[int, int, int, int] | None:
            """Return (x, y, width, height) or None if cancelled."""
            if not self._confirmed or not self._start or not self._end:
                return None
            rect = QRect(self._start, self._end).normalized()
            if rect.width() < 10 or rect.height() < 10:
                return None
            return (rect.x(), rect.y(), rect.width(), rect.height())

    # QApplication must exist before creating any QPixmap
    app = QApplication(sys.argv)

    # Capture a screenshot of the desktop before showing the picker window
    with mss.mss() as sct:
        monitor = sct.monitors[0]  # entire virtual screen
        shot = sct.grab(monitor)
        frame = np.array(shot, dtype=np.uint8)  # BGRA

    # Convert BGRA numpy array → QPixmap
    h, w, _ = frame.shape
    # BGRA → RGBA for QImage
    frame_rgba = frame.copy()
    frame_rgba[:, :, 0], frame_rgba[:, :, 2] = (
        frame[:, :, 2].copy(),
        frame[:, :, 0].copy(),
    )
    qimage = QImage(frame_rgba.data, w, h, w * 4, QImage.Format.Format_RGBA8888)
    bg_pixmap = QPixmap.fromImage(qimage)

    picker = RoiPickerWindow(config_path, mode, bg_pixmap)
    app.exec()

    roi = picker.selected_roi
    if roi is None:
        print("Calibration cancelled.")
        return None

    x, y, w, h = roi
    print(f"Selected region: {w}x{h} at ({x}, {y})")

    section = _MODES[mode]["section"]
    _save_roi_to_config(roi, Path(config_path), section=section)
    print(f"Saved [{section}] ROI to {config_path}")
    return roi
