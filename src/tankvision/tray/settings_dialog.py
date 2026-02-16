"""Settings dialog for key configuration fields."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLineEdit,
    QPushButton,
    QWidget,
)

from tankvision.config import load_config
from tankvision.tray.state_bridge import AppStateBridge


class SettingsDialog(QDialog):
    """Simple dialog for editing key config values at runtime."""

    def __init__(
        self,
        config_path: str,
        bridge: AppStateBridge,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("WoT Console Assistant — Settings")
        # WindowStaysOnTopHint ensures the dialog appears above other windows
        # when opened from a macOS tray icon (which doesn't activate the app).
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint
        )
        self._bridge = bridge

        config = load_config(config_path)

        layout = QFormLayout(self)

        # Gamertag
        self._orig_gamertag = config["player"]["gamertag"]
        self._gamertag = QLineEdit(self._orig_gamertag)
        layout.addRow("Gamertag:", self._gamertag)

        # Platform
        self._orig_platform = config["player"]["platform"]
        self._platform = QComboBox()
        self._platform.addItems(["xbox", "ps"])
        self._platform.setCurrentText(self._orig_platform)
        layout.addRow("Platform:", self._platform)

        # Sample rate
        self._orig_sample_rate = config["ocr"]["sample_rate"]
        self._sample_rate = QDoubleSpinBox()
        self._sample_rate.setRange(0.5, 10.0)
        self._sample_rate.setSingleStep(0.5)
        self._sample_rate.setDecimals(1)
        self._sample_rate.setValue(self._orig_sample_rate)
        self._sample_rate.setSuffix(" Hz")
        layout.addRow("Sample Rate:", self._sample_rate)

        # Confidence threshold
        self._orig_confidence = config["ocr"]["confidence_threshold"]
        self._confidence = QDoubleSpinBox()
        self._confidence.setRange(0.1, 1.0)
        self._confidence.setSingleStep(0.05)
        self._confidence.setDecimals(2)
        self._confidence.setValue(self._orig_confidence)
        layout.addRow("OCR Confidence:", self._confidence)

        # Buttons — "Save" instead of "Ok" so intent is clear
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        save_btn = QPushButton("Save")
        save_btn.setDefault(True)
        buttons.addButton(save_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self._apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _apply(self) -> None:
        """Push only changed values to the bridge for the worker to pick up."""
        if self._gamertag.text() != self._orig_gamertag:
            self._bridge.push_config_change("player", "gamertag", self._gamertag.text())
        if self._platform.currentText() != self._orig_platform:
            self._bridge.push_config_change("player", "platform", self._platform.currentText())
        if self._sample_rate.value() != self._orig_sample_rate:
            self._bridge.push_config_change("ocr", "sample_rate", self._sample_rate.value())
        if self._confidence.value() != self._orig_confidence:
            self._bridge.push_config_change(
                "ocr", "confidence_threshold", self._confidence.value()
            )
        self.accept()
