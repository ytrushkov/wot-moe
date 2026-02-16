"""Floating window showing live OCR capture and recognition results."""

from __future__ import annotations

import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QGroupBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from tankvision.tray.state_bridge import AppSnapshot, AppStateBridge


class OcrValidationWindow(QWidget):
    """Shows the current captured frame, recognized text, and confidence."""

    def __init__(self, bridge: AppStateBridge, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bridge = bridge

        self.setWindowTitle("TankVision — OCR Preview")
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setMinimumSize(400, 300)

        layout = QVBoxLayout(self)

        # Frame display
        frame_group = QGroupBox("Captured Frame")
        frame_layout = QVBoxLayout(frame_group)
        self._frame_label = QLabel("No frame yet")
        self._frame_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._frame_label.setMinimumHeight(150)
        self._frame_label.setScaledContents(True)
        frame_layout.addWidget(self._frame_label)
        layout.addWidget(frame_group)

        # Recognition results
        result_group = QGroupBox("Recognition")
        result_layout = QVBoxLayout(result_group)
        self._text_label = QLabel("Recognized: --")
        self._confidence_label = QLabel("Confidence: --")
        self._rate_label = QLabel("Sample rate: --")
        self._digits_label = QLabel("Digits: --")
        result_layout.addWidget(self._text_label)
        result_layout.addWidget(self._confidence_label)
        result_layout.addWidget(self._digits_label)
        result_layout.addWidget(self._rate_label)
        layout.addWidget(result_group)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._bridge.set_ocr_preview_active(True)

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self._bridge.set_ocr_preview_active(False)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._bridge.set_ocr_preview_active(False)
        super().closeEvent(event)

    def update_snapshot(self, snapshot: AppSnapshot) -> None:
        """Update display with a new state snapshot from the worker."""
        if snapshot.last_frame is not None:
            self._display_frame(snapshot.last_frame)

        self._text_label.setText(f"Recognized: {snapshot.last_ocr_text or 'nothing'}")
        self._confidence_label.setText(f"Confidence: {snapshot.last_confidence:.1%}")
        self._rate_label.setText(f"Actual sample rate: {snapshot.sample_rate_actual:.1f} Hz")

    def _display_frame(self, frame: np.ndarray) -> None:
        """Convert a BGR numpy array to QPixmap and display."""
        h, w = frame.shape[:2]
        if frame.ndim == 3 and frame.shape[2] >= 3:
            # BGR to RGB
            rgb = frame[:, :, :3][:, :, ::-1].copy()
            qimg = QImage(rgb.data, w, h, w * 3, QImage.Format.Format_RGB888)
        else:
            # Grayscale
            gray = frame.copy()
            qimg = QImage(gray.data, w, h, w, QImage.Format.Format_Grayscale8)
        self._frame_label.setPixmap(QPixmap.fromImage(qimg))
