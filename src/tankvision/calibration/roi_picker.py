"""Visual ROI picker: transparent overlay where the user drags a rectangle.

Two-step flow:
1. Dialog to select which screen/window to capture (with thumbnails).
2. Fullscreen overlay on that screen to drag-select the ROI.
"""

from __future__ import annotations

import sys
import time
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

# Thumbnail size for the screen picker dialog
_THUMB_WIDTH = 240
_THUMB_HEIGHT = 135


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


def _grab_mss_monitor(sct, monitor: dict) -> tuple:
    """Capture a monitor region and return (numpy_bgra_array, monitor_dict)."""
    import numpy as np

    shot = sct.grab(monitor)
    return np.array(shot, dtype=np.uint8), monitor


def _bgra_to_qpixmap(frame, QImage, QPixmap):
    """Convert a BGRA numpy array to a QPixmap."""
    h, w, _ = frame.shape
    frame_rgba = frame.copy()
    frame_rgba[:, :, 0], frame_rgba[:, :, 2] = (
        frame[:, :, 2].copy(),
        frame[:, :, 0].copy(),
    )
    qimage = QImage(frame_rgba.data, w, h, w * 4, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(qimage.copy())


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
        from PyQt6.QtCore import QPoint, QRect, QSize, Qt, QTimer
        from PyQt6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPen, QPixmap
        from PyQt6.QtWidgets import (
            QApplication,
            QDialog,
            QHBoxLayout,
            QLabel,
            QListWidget,
            QListWidgetItem,
            QMainWindow,
            QPushButton,
            QVBoxLayout,
        )
    except ImportError:
        print(
            "PyQt6 is required for the calibration UI.\n"
            "Install it with: pip install 'wot-console-overlay[ui]'"
        )
        return None

    import mss
    import numpy as np

    mode_info = _MODES[mode]

    # ---- Step 1: Screen selection dialog with thumbnails ----

    class ScreenPickerDialog(QDialog):
        """Dialog showing screen thumbnails for the user to choose."""

        def __init__(self, thumbnails: list[tuple[QPixmap, str, int]]) -> None:
            super().__init__()
            self.selected_index: int | None = None

            self.setWindowTitle("TankVision — Choose Screen")
            self.setMinimumWidth(max(400, (_THUMB_WIDTH + 40) * len(thumbnails) + 40))

            layout = QVBoxLayout(self)

            label = QLabel(
                "Select the screen where your game is running, "
                "then click Continue to draw the capture region."
            )
            label.setWordWrap(True)
            layout.addWidget(label)

            # Thumbnail grid
            thumb_layout = QHBoxLayout()
            self._list = QListWidget()
            self._list.setViewMode(QListWidget.ViewMode.IconMode)
            self._list.setIconSize(QSize(_THUMB_WIDTH, _THUMB_HEIGHT))
            self._list.setSpacing(12)
            self._list.setMovement(QListWidget.Movement.Static)
            self._list.setResizeMode(QListWidget.ResizeMode.Adjust)
            self._list.setMinimumHeight(_THUMB_HEIGHT + 60)
            self._list.setWordWrap(True)

            for pixmap, label_text, idx in thumbnails:
                icon = QIcon(pixmap)
                item = QListWidgetItem(icon, label_text)
                item.setData(Qt.ItemDataRole.UserRole, idx)
                item.setSizeHint(QSize(_THUMB_WIDTH + 20, _THUMB_HEIGHT + 40))
                self._list.addItem(item)

            if self._list.count() > 0:
                self._list.setCurrentRow(0)
            self._list.itemDoubleClicked.connect(self._accept)
            layout.addWidget(self._list)

            btn_layout = QHBoxLayout()
            cancel_btn = QPushButton("Cancel")
            cancel_btn.clicked.connect(self.reject)
            btn_layout.addWidget(cancel_btn)

            ok_btn = QPushButton("Continue")
            ok_btn.setDefault(True)
            ok_btn.clicked.connect(self._accept)
            btn_layout.addWidget(ok_btn)

            layout.addLayout(btn_layout)

        def _accept(self) -> None:
            item = self._list.currentItem()
            if item is not None:
                self.selected_index = item.data(Qt.ItemDataRole.UserRole)
            self.accept()

    # ---- Step 2: ROI picker overlay ----

    class RoiPickerWindow(QMainWindow):
        """Fullscreen window showing a screenshot with a selection overlay."""

        def __init__(self, background: QPixmap, screen_offset: QPoint) -> None:
            super().__init__()
            self._bg = background
            self._screen_offset = screen_offset
            self._start: QPoint | None = None
            self._end: QPoint | None = None
            self._confirmed = False

            self.setWindowTitle(mode_info["title"])
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
            )

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
                "\n\n" + mode_info["instruction"],
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

                # Show dimensions + absolute coordinates
                abs_x = rect.x() + self._screen_offset.x()
                abs_y = rect.y() + self._screen_offset.y()
                dim_font = QFont("Arial", 12)
                painter.setFont(dim_font)
                painter.setPen(QPen(QColor(0, 255, 0)))
                painter.drawText(
                    rect.x(),
                    rect.y() - 5,
                    f"{rect.width()}x{rect.height()} at ({abs_x}, {abs_y})",
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
            """Return absolute (x, y, width, height) or None if cancelled."""
            if not self._confirmed or not self._start or not self._end:
                return None
            rect = QRect(self._start, self._end).normalized()
            if rect.width() < 10 or rect.height() < 10:
                return None
            # Convert window-local coords to absolute screen coords
            abs_x = rect.x() + self._screen_offset.x()
            abs_y = rect.y() + self._screen_offset.y()
            return (abs_x, abs_y, rect.width(), rect.height())

    # ---- Launch ----

    app = QApplication(sys.argv)

    # Capture thumbnails for each screen
    screens = QApplication.screens()
    thumbnails: list[tuple[QPixmap, str, int]] = []
    with mss.mss() as sct:
        for i, screen in enumerate(screens):
            geo = screen.geometry()
            mss_idx = i + 1
            if mss_idx < len(sct.monitors):
                monitor = sct.monitors[mss_idx]
            else:
                monitor = {
                    "left": geo.x(), "top": geo.y(),
                    "width": geo.width(), "height": geo.height(),
                }
            shot = sct.grab(monitor)
            frame = np.array(shot, dtype=np.uint8)
            pixmap = _bgra_to_qpixmap(frame, QImage, QPixmap)
            thumb = pixmap.scaled(
                _THUMB_WIDTH, _THUMB_HEIGHT,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            name = screen.name() or f"Screen {i + 1}"
            label = f"{name}\n{geo.width()}x{geo.height()}"
            thumbnails.append((thumb, label, i))

    # Step 1: show the screen picker
    dialog = ScreenPickerDialog(thumbnails)
    if dialog.exec() != QDialog.DialogCode.Accepted or dialog.selected_index is None:
        print("Calibration cancelled.")
        return None

    screen_idx = dialog.selected_index
    screen = screens[screen_idx]
    geo = screen.geometry()
    print(
        f"Selected screen: {screen.name()} "
        f"({geo.width()}x{geo.height()} at {geo.x()},{geo.y()})"
    )

    # Close the dialog and wait for it to fully disappear
    dialog.close()
    dialog.deleteLater()
    app.processEvents()
    time.sleep(0.5)

    # Step 2: take a fresh screenshot (dialog is now gone)
    mss_monitor_idx = screen_idx + 1
    with mss.mss() as sct:
        if mss_monitor_idx < len(sct.monitors):
            monitor = sct.monitors[mss_monitor_idx]
        else:
            monitor = {
                "left": geo.x(), "top": geo.y(),
                "width": geo.width(), "height": geo.height(),
            }
        shot = sct.grab(monitor)
        frame = np.array(shot, dtype=np.uint8)

    bg_pixmap = _bgra_to_qpixmap(frame, QImage, QPixmap)

    # Step 3: show the ROI picker on the selected screen
    screen_offset = QPoint(geo.x(), geo.y())
    picker = RoiPickerWindow(bg_pixmap, screen_offset)
    picker.setGeometry(geo)
    picker.showFullScreen()
    app.exec()

    roi = picker.selected_roi
    if roi is None:
        print("Calibration cancelled.")
        return None

    x, y, w, h = roi
    print(f"Selected region: {w}x{h} at ({x}, {y})")

    section = mode_info["section"]
    _save_roi_to_config(roi, Path(config_path), section=section)
    print(f"Saved [{section}] ROI to {config_path}")
    return roi
