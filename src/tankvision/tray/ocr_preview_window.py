"""Floating window showing live capture and recognition results."""

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
    """Shows the current captured frames, recognized text, and confidence."""

    def __init__(self, bridge: AppStateBridge, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bridge = bridge

        self.setWindowTitle("WoT Console Assistant — Capture Preview")
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setMinimumSize(400, 400)

        layout = QVBoxLayout(self)

        # Damage capture area
        damage_group = QGroupBox("Damage Region")
        damage_layout = QVBoxLayout(damage_group)
        self._frame_label = QLabel("No frame yet")
        self._frame_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._frame_label.setMinimumHeight(100)
        self._frame_label.setScaledContents(True)
        damage_layout.addWidget(self._frame_label)
        layout.addWidget(damage_group)

        # Garage capture area
        garage_group = QGroupBox("Garage Region (Tank Name)")
        garage_layout = QVBoxLayout(garage_group)
        self._garage_label = QLabel("No frame yet")
        self._garage_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._garage_label.setMinimumHeight(100)
        self._garage_label.setScaledContents(True)
        garage_layout.addWidget(self._garage_label)
        layout.addWidget(garage_group)

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
            self._display_frame(snapshot.last_frame, self._frame_label)
        if snapshot.garage_frame is not None:
            self._display_frame(snapshot.garage_frame, self._garage_label)

        self._text_label.setText(f"Recognized: {snapshot.last_ocr_text or 'nothing'}")
        self._confidence_label.setText(f"Confidence: {snapshot.last_confidence:.1%}")
        self._rate_label.setText(f"Actual sample rate: {snapshot.sample_rate_actual:.1f} Hz")

    @staticmethod
    def _display_frame(frame: np.ndarray, label: QLabel) -> None:
        """Convert a BGR numpy array to QPixmap and display in the given label."""
        h, w = frame.shape[:2]
        if frame.ndim == 3 and frame.shape[2] >= 3:
            # BGR to RGB
            rgb = frame[:, :, :3][:, :, ::-1].copy()
            qimg = QImage(rgb.data, w, h, w * 3, QImage.Format.Format_RGB888)
        else:
            # Grayscale
            gray = frame.copy()
            qimg = QImage(gray.data, w, h, w, QImage.Format.Format_Grayscale8)
        label.setPixmap(QPixmap.fromImage(qimg))
