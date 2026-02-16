"""Floating window showing recent log output."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from tankvision.tray.state_bridge import AppStateBridge


class LogViewerWindow(QWidget):
    """Scrollable log viewer connected to the AppStateBridge.log_message signal."""

    MAX_LINES = 500

    def __init__(self, bridge: AppStateBridge, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("WoT Console Assistant — Log")
        self.setWindowFlags(Qt.WindowType.Tool)
        self.setMinimumSize(640, 400)

        layout = QVBoxLayout(self)

        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setMaximumBlockCount(self.MAX_LINES)
        layout.addWidget(self._text)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._text.clear)
        btn_layout.addWidget(clear_btn)
        layout.addLayout(btn_layout)

        # Use QueuedConnection explicitly: the signal is emitted from the
        # asyncio worker thread, so the slot must be marshalled to the main
        # thread's event loop.
        bridge.log_message.connect(
            self._append_line, Qt.ConnectionType.QueuedConnection
        )

    def _append_line(self, line: str) -> None:
        self._text.appendPlainText(line)
