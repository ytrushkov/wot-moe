"""Settings dialog for key configuration fields."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLineEdit,
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
        self.setWindowTitle("TankVision — Settings")
        self._bridge = bridge

        config = load_config(config_path)

        layout = QFormLayout(self)

        # Gamertag
        self._gamertag = QLineEdit(config["player"]["gamertag"])
        layout.addRow("Gamertag:", self._gamertag)

        # Platform
        self._platform = QComboBox()
        self._platform.addItems(["xbox", "ps"])
        self._platform.setCurrentText(config["player"]["platform"])
        layout.addRow("Platform:", self._platform)

        # Sample rate
        self._sample_rate = QDoubleSpinBox()
        self._sample_rate.setRange(0.5, 30.0)
        self._sample_rate.setSingleStep(0.5)
        self._sample_rate.setDecimals(1)
        self._sample_rate.setValue(config["ocr"]["sample_rate"])
        self._sample_rate.setSuffix(" Hz")
        layout.addRow("Sample Rate:", self._sample_rate)

        # Confidence threshold
        self._confidence = QDoubleSpinBox()
        self._confidence.setRange(0.1, 1.0)
        self._confidence.setSingleStep(0.05)
        self._confidence.setDecimals(2)
        self._confidence.setValue(config["ocr"]["confidence_threshold"])
        layout.addRow("OCR Confidence:", self._confidence)

        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _apply(self) -> None:
        """Push changes to the bridge for the worker to pick up."""
        self._bridge.push_config_change("player", "gamertag", self._gamertag.text())
        self._bridge.push_config_change("player", "platform", self._platform.currentText())
        self._bridge.push_config_change("ocr", "sample_rate", self._sample_rate.value())
        self._bridge.push_config_change(
            "ocr", "confidence_threshold", self._confidence.value()
        )
        self.accept()
