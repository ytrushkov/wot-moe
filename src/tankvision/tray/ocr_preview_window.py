"""Floating window showing live capture and recognition results."""

from __future__ import annotations

import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QLabel,
    QTabWidget,
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
        self.setMinimumSize(420, 350)

        layout = QVBoxLayout(self)

        # Tab widget
        self._tabs = QTabWidget()
        layout.addWidget(self._tabs)

        # --- Damage Region tab ---
        damage_tab = QWidget()
        dl = QVBoxLayout(damage_tab)
        self._damage_frame = QLabel("No frame yet")
        self._damage_frame.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._damage_frame.setMinimumHeight(120)
        self._damage_frame.setScaledContents(True)
        dl.addWidget(self._damage_frame)
        self._damage_text = QLabel("Recognized: --")
        self._damage_confidence = QLabel("Confidence: --")
        self._damage_rate = QLabel("Sample rate: --")
        dl.addWidget(self._damage_text)
        dl.addWidget(self._damage_confidence)
        dl.addWidget(self._damage_rate)
        dl.addStretch()
        self._tabs.addTab(damage_tab, "Damage Region")

        # --- Garage (Tank Name) tab ---
        garage_tab = QWidget()
        gl = QVBoxLayout(garage_tab)
        self._garage_frame = QLabel("No frame yet")
        self._garage_frame.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._garage_frame.setMinimumHeight(120)
        self._garage_frame.setScaledContents(True)
        gl.addWidget(self._garage_frame)
        self._garage_tank = QLabel("Detected tank: --")
        gl.addWidget(self._garage_tank)
        gl.addStretch()
        self._tabs.addTab(garage_tab, "Tank Name")

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
        # Damage tab
        if snapshot.last_frame is not None:
            self._set_frame(snapshot.last_frame, self._damage_frame)
        self._damage_text.setText(
            f"Recognized: {snapshot.last_ocr_text or 'nothing'}"
        )
        self._damage_confidence.setText(
            f"Confidence: {snapshot.last_confidence:.1%}"
        )
        self._damage_rate.setText(
            f"Sample rate: {snapshot.sample_rate_actual:.1f} Hz"
        )

        # Garage tab
        if snapshot.garage_frame is not None:
            self._set_frame(snapshot.garage_frame, self._garage_frame)
        self._garage_tank.setText(
            f"Detected tank: {snapshot.tank_name or '--'}"
        )

    @staticmethod
    def _set_frame(frame: np.ndarray, label: QLabel) -> None:
        """Convert a BGR numpy array to QPixmap and display in the given label."""
        h, w = frame.shape[:2]
        if frame.ndim == 3 and frame.shape[2] >= 3:
            rgb = frame[:, :, :3][:, :, ::-1].copy()
            qimg = QImage(rgb.data, w, h, w * 3, QImage.Format.Format_RGB888)
        else:
            gray = frame.copy()
            qimg = QImage(gray.data, w, h, w, QImage.Format.Format_Grayscale8)
        label.setPixmap(QPixmap.fromImage(qimg))
