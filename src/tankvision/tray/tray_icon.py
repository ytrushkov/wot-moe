"""System tray icon with context menu."""

from __future__ import annotations

import logging
import platform
import time
import webbrowser
from pathlib import Path

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPixmap
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

from tankvision.tray.state_bridge import AppSnapshot, AppStateBridge

logger = logging.getLogger(__name__)

_ASSETS_DIR = Path(__file__).parent / "assets"

# Throttle: don't update the UI faster than this (seconds).
_MIN_UPDATE_INTERVAL = 0.1  # 10 Hz


def _make_circle_icon(color: QColor) -> QIcon:
    """Create a simple colored circle icon for the tray."""
    pm = QPixmap(64, 64)
    pm.fill(QColor(0, 0, 0, 0))
    from PyQt6.QtGui import QPainter

    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(color)
    painter.setPen(QColor(255, 255, 255, 180))
    painter.drawEllipse(4, 4, 56, 56)
    painter.end()
    return QIcon(pm)


def _load_icon(name: str, fallback_color: QColor) -> QIcon:
    """Load a tray icon from assets, or generate a colored circle fallback."""
    path = _ASSETS_DIR / f"{name}.png"
    if path.exists():
        return QIcon(str(path))
    return _make_circle_icon(fallback_color)


def _activate_app() -> None:
    """Bring the application to the foreground on macOS.

    Without this, windows opened from a tray icon menu appear behind
    other applications because the app is not the "active" application.
    """
    if platform.system() == "Darwin":
        app = QApplication.instance()
        if app is not None:
            # processEvents ensures pending events are flushed before activation
            app.processEvents()
    # On macOS use Cocoa API to steal focus
    try:
        from AppKit import NSApp, NSApplicationActivationPolicyRegular

        NSApp.setActivationPolicy_(NSApplicationActivationPolicyRegular)
        NSApp.activateIgnoringOtherApps_(True)
    except ImportError:
        pass


class TankVisionTrayIcon(QSystemTrayIcon):
    """System tray icon with full context menu for TankVision."""

    quit_requested = pyqtSignal()

    def __init__(
        self,
        bridge: AppStateBridge,
        config_path: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._config_path = config_path
        self._last_ui_update = 0.0

        # Lazy-created windows
        self._ocr_window = None
        self._log_window = None

        # Load icons
        self._icon_idle = _load_icon("icon_idle", QColor(128, 128, 128))
        self._icon_active = _load_icon("icon_active", QColor(0, 200, 0))
        self._icon_paused = _load_icon("icon_paused", QColor(220, 180, 0))
        self.setIcon(self._icon_idle)
        self.setToolTip("WoT Console Assistant — Starting...")

        # Build context menu
        self._menu = QMenu()
        self._build_menu()
        self.setContextMenu(self._menu)

        # Connect bridge signals
        bridge.state_updated.connect(self._on_state_updated)

    def _build_menu(self) -> None:
        menu = self._menu
        menu.clear()

        # --- Status info (disabled, read-only) ---
        self._status_action = menu.addAction("Status: starting...")
        self._status_action.setEnabled(False)
        self._tank_action = menu.addAction("Tank: --")
        self._tank_action.setEnabled(False)
        self._moe_action = menu.addAction("MoE: --")
        self._moe_action.setEnabled(False)

        menu.addSeparator()

        # --- Capture controls ---
        self._resume_action = menu.addAction("Resume Capture")
        self._resume_action.triggered.connect(self._on_resume)
        self._resume_action.setVisible(False)

        self._pause_action = menu.addAction("Pause Capture")
        self._pause_action.triggered.connect(self._on_pause)

        menu.addSeparator()

        # --- Capture preview ---
        ocr_action = menu.addAction("Capture Preview...")
        ocr_action.triggered.connect(self._show_ocr_preview)

        menu.addSeparator()

        # --- Calibrate submenu ---
        cal_menu = menu.addMenu("Calibrate")
        cal_ocr = cal_menu.addAction("OCR Region...")
        cal_ocr.triggered.connect(lambda: self._run_calibration("ocr"))
        cal_garage = cal_menu.addAction("Garage Region...")
        cal_garage.triggered.connect(lambda: self._run_calibration("garage"))

        menu.addSeparator()

        # --- Overlay ---
        overlay_action = menu.addAction("Open Overlay in Browser")
        overlay_action.triggered.connect(self._open_overlay)

        # --- Settings ---
        settings_action = menu.addAction("Settings...")
        settings_action.triggered.connect(self._show_settings)

        # --- Log viewer ---
        log_action = menu.addAction("View Log...")
        log_action.triggered.connect(self._show_log_viewer)

        menu.addSeparator()

        # --- Quit ---
        quit_action = menu.addAction("Quit")
        quit_action.triggered.connect(self.quit_requested.emit)

    # --- Signal handler ---

    def _on_state_updated(self, snapshot: AppSnapshot) -> None:
        """Receive state updates from the worker thread (via signal)."""
        now = time.monotonic()
        if now - self._last_ui_update < _MIN_UPDATE_INTERVAL:
            # Still forward to OCR preview even when throttled
            if self._ocr_window and self._ocr_window.isVisible():
                self._ocr_window.update_snapshot(snapshot)
            return
        self._last_ui_update = now

        # Update tooltip
        tank = snapshot.tank_name or "No tank"
        self.setToolTip(
            f"WoT Console Assistant — {tank}\n"
            f"MoE: {snapshot.moe_percent:.2f}% | {snapshot.status}"
        )

        # Update icon
        if snapshot.status == "paused":
            self.setIcon(self._icon_paused)
        elif snapshot.status == "battle_active":
            self.setIcon(self._icon_active)
        else:
            self.setIcon(self._icon_idle)

        # Update menu status lines
        self._status_action.setText(f"Status: {snapshot.status}")
        self._tank_action.setText(f"Tank: {snapshot.tank_name or '--'}")
        self._moe_action.setText(f"MoE: {snapshot.moe_percent:.2f}%")

        # Forward to OCR preview window if open
        if self._ocr_window and self._ocr_window.isVisible():
            self._ocr_window.update_snapshot(snapshot)

    # --- Capture controls ---

    def _on_pause(self) -> None:
        self._bridge.request_pause()
        self._pause_action.setVisible(False)
        self._resume_action.setVisible(True)

    def _on_resume(self) -> None:
        self._bridge.request_resume()
        self._resume_action.setVisible(False)
        self._pause_action.setVisible(True)

    # --- Windows ---

    def _show_ocr_preview(self) -> None:
        from tankvision.tray.ocr_preview_window import OcrValidationWindow

        _activate_app()
        if self._ocr_window is None:
            self._ocr_window = OcrValidationWindow(self._bridge)
        self._ocr_window.show()
        self._ocr_window.raise_()
        self._ocr_window.activateWindow()

    def _show_settings(self) -> None:
        from tankvision.tray.settings_dialog import SettingsDialog

        _activate_app()
        dialog = SettingsDialog(self._config_path, self._bridge)
        dialog.raise_()
        dialog.activateWindow()
        dialog.exec()

    def _show_log_viewer(self) -> None:
        from tankvision.tray.log_viewer_window import LogViewerWindow

        _activate_app()
        if self._log_window is None:
            self._log_window = LogViewerWindow(self._bridge)
        self._log_window.show()
        self._log_window.raise_()
        self._log_window.activateWindow()

    def _run_calibration(self, mode: str) -> None:
        from tankvision.calibration.roi_picker import run_roi_picker

        run_roi_picker(self._config_path, mode=mode)

    def _open_overlay(self) -> None:
        from tankvision.config import load_config

        config = load_config(self._config_path)
        port = config["server"]["http_port"]
        webbrowser.open(f"http://localhost:{port}")
